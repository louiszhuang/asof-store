import json
import pickle
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import blake2b
from typing import TYPE_CHECKING, Any
from uuid import UUID

import polars as pl

from ._abc import AsOfStoreABC, _matches_fields, _matches_terms

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons

_SORTABLE_TIMESTAMP_TYPES = {
    int,
    float,
    str,
    bytes,
    datetime,
    date,
    Decimal,
    UUID,
}
_JSON_TYPES = (dict, list, tuple)


def _is_pydantic_model(python_type: type[Any]) -> bool:
    try:
        from pydantic import BaseModel
    except ImportError:
        return False
    return isinstance(python_type, type) and issubclass(python_type, BaseModel)


def _uses_json(python_type: type[Any]) -> bool:
    return python_type in _JSON_TYPES or _is_pydantic_model(python_type)


def _uses_pickle(python_type: type[Any]) -> bool:
    native_types = {
        bool,
        int,
        float,
        str,
        bytes,
        datetime,
        date,
        Decimal,
        UUID,
        dict,
        list,
        tuple,
        type(None),
    }
    return python_type not in native_types and not _is_pydantic_model(python_type)


def _encode(python_type: type[Any], value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, python_type):
        raise TypeError(
            f"Expected value of type {python_type.__name__}, got {type(value).__name__}"
        )
    if not _uses_pickle(python_type) and type(value) is not python_type:
        raise TypeError(
            f"Expected value of type {python_type.__name__}, got {type(value).__name__}"
        )
    if _uses_pickle(python_type):
        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    if _is_pydantic_model(python_type):
        return value.model_dump(mode="json", exclude_unset=True)
    if python_type is tuple:
        return list(value)
    if python_type is datetime:
        if value.utcoffset() is None:
            raise ValueError("SQL-backed datetime values must be timezone-aware")
        return value.astimezone(UTC)
    return value


def _decode(python_type: type[Any], value: Any) -> Any:
    if value is None:
        return None
    if _uses_pickle(python_type):
        return pickle.loads(value)
    if _uses_json(python_type):
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if isinstance(value, str):
            value = json.loads(value)
        if _is_pydantic_model(python_type):
            return python_type.model_validate(value)
        if python_type is tuple:
            return tuple(value)
        return value
    if python_type is datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if python_type is date and isinstance(value, str):
        return date.fromisoformat(value)
    if python_type is Decimal and not isinstance(value, Decimal):
        return Decimal(value)
    if python_type is UUID and isinstance(value, bytes):
        return UUID(bytes=value)
    if python_type is UUID and not isinstance(value, UUID):
        return UUID(str(value))
    return value


def _database_value(python_type: type[Any], value: Any, *, postgres: bool) -> Any:
    encoded = _encode(python_type, value)
    if value is None:
        return None
    if _uses_json(python_type):
        return json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))
    if python_type is datetime and not postgres:
        return encoded.isoformat()
    if python_type is date and not postgres:
        return encoded.isoformat()
    if python_type is Decimal:
        return str(encoded)
    if python_type is UUID:
        return str(encoded)
    return encoded


def _sql_type(python_type: type[Any], *, postgres: bool) -> str:
    if _uses_json(python_type):
        return "JSONB" if postgres else "TEXT"
    if _uses_pickle(python_type) or python_type is bytes:
        return "BYTEA" if postgres else "BLOB"
    if python_type is bool:
        return "BOOLEAN"
    if python_type is int:
        return "BIGINT"
    if python_type is float:
        return "DOUBLE PRECISION" if postgres else "REAL"
    if python_type is str:
        return "TEXT"
    if python_type is datetime:
        return "TIMESTAMPTZ" if postgres else "TEXT"
    if python_type is date:
        return "DATE" if postgres else "TEXT"
    if python_type is Decimal:
        return "NUMERIC" if postgres else "TEXT"
    if python_type is UUID:
        return "UUID" if postgres else "TEXT"
    if python_type is type(None):
        return "BOOLEAN"
    raise TypeError(f"Unsupported SQL value type: {python_type.__name__}")


class SqlBackend[Timestamp: SupportsAllComparisons, Key, Value](
    AsOfStoreABC[Timestamp, Key, Value]
):
    def __init__(
        self,
        sql_uri: str,
        table_name: str,
        timestamp_type: type[Timestamp],
        key_type: type[Key],
        value_type: type[Value],
    ) -> None:
        if not isinstance(table_name, str) or not table_name.strip():
            raise ValueError("table_name must be a non-empty string")
        if "\x00" in table_name:
            raise ValueError("table_name must not contain null characters")

        for name, python_type in (
            ("timestamp_type", timestamp_type),
            ("key_type", key_type),
            ("value_type", value_type),
        ):
            if not isinstance(python_type, type):
                raise TypeError(f"{name} must be a Python type")

        if timestamp_type not in _SORTABLE_TIMESTAMP_TYPES:
            supported_types = ", ".join(
                sorted(python_type.__name__ for python_type in _SORTABLE_TIMESTAMP_TYPES)
            )
            raise TypeError(
                f"timestamp_type must map to an indexable, sortable SQL scalar "
                f"type ({supported_types}); got {timestamp_type.__name__}"
            )

        self._timestamp_type = timestamp_type
        self._key_type = key_type
        self._value_type = value_type
        self._table_name = table_name
        self._quoted_table = '"' + table_name.replace('"', '""') + '"'
        self._postgres = sql_uri.startswith(("postgresql://", "postgres://"))

        if self._postgres:
            try:
                from adbc_driver_postgresql import dbapi
            except ImportError as exc:
                raise ImportError(
                    "PostgreSQL storage requires ADBC; install "
                    "'asof-store[sql-postgres]'"
                ) from exc
            self._connection = dbapi.connect(sql_uri)
        elif sql_uri.startswith("sqlite:///"):
            try:
                from adbc_driver_sqlite import dbapi
            except ImportError as exc:
                raise ImportError(
                    "SQLite storage requires ADBC; install 'asof-store[sql-sqlite]'"
                ) from exc
            database = sql_uri.removeprefix("sqlite:///")
            if sql_uri.startswith("sqlite:////"):
                database = "/" + database
            self._connection = dbapi.connect(database)
        else:
            raise ValueError(
                "SQL storage supports SQLite and PostgreSQL ADBC URIs; got "
                f"{sql_uri!r}"
            )

        columns = [
            f'"timestamp" {_sql_type(timestamp_type, postgres=self._postgres)} NOT NULL',
            f'"key" {_sql_type(key_type, postgres=self._postgres)} NOT NULL',
        ]
        if value_type is not type(None):
            columns.append(
                f'"value" {_sql_type(value_type, postgres=self._postgres)}'
            )
        columns.append('PRIMARY KEY ("key", "timestamp")')
        self._execute(
            f"CREATE TABLE IF NOT EXISTS {self._quoted_table} "
            f"({', '.join(columns)})"
        )
        self._connection.commit()

    def _execute(self, statement: str, parameters: Sequence[Any] = ()) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(statement, tuple(parameters))

    def _read(self, statement: str, parameters: Sequence[Any] = ()) -> pl.DataFrame:
        return pl.read_database(
            statement,
            self._connection,
            execute_options={"parameters": tuple(parameters)},
        )

    def _parameter(self, python_type: type[Any], position: int = 1) -> str:
        marker = f"${position}" if self._postgres else "?"
        if self._postgres:
            postgres_types = {
                Decimal: "NUMERIC",
                UUID: "UUID",
                datetime: "TIMESTAMPTZ",
                date: "DATE",
            }
            if _uses_json(python_type):
                return f"CAST({marker} AS JSONB)"
            if python_type in postgres_types:
                return f"CAST({marker} AS {postgres_types[python_type]})"
        return marker

    def _timestamp_column(self) -> str:
        if self._postgres and self._timestamp_type is datetime:
            return 'CAST("timestamp" AS TEXT) AS "timestamp"'
        return '"timestamp"'

    def close(self) -> None:
        self._connection.close()

    def create_trigram_indexes(
        self, fields: Sequence[str], *, column: str
    ) -> list[str]:
        """Create an idempotent multi-column GIN trigram index for JSON fields."""
        if not self._postgres:
            raise ValueError("trigram indexes are supported only by PostgreSQL")
        if column not in {"key", "value"}:
            raise ValueError("column must be 'key' or 'value'")
        python_type = self._key_type if column == "key" else self._value_type
        if not (python_type is dict or _is_pydantic_model(python_type)):
            raise TypeError(f"trigram indexes require JSON-compatible {column} data")

        supplied_fields = tuple(fields)
        if not supplied_fields:
            raise ValueError("at least one field is required")
        if any(
            not isinstance(field, str)
            or not field
            or any(
                not part
                or not (
                    part[0].isascii()
                    and (part[0].isalpha() or part[0] == "_")
                )
                or not all(
                    char.isascii() and (char.isalnum() or char == "_")
                    for char in part
                )
                for part in field.split(".")
            )
            for field in supplied_fields
        ):
            raise ValueError("field names must be dotted ASCII identifiers")
        unique_fields = tuple(dict.fromkeys(supplied_fields))

        self._execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        self._connection.commit()

        table_hash = blake2b(self._table_name.encode(), digest_size=4).hexdigest()
        fields_hash = blake2b(
            "\0".join(unique_fields).encode(), digest_size=6
        ).hexdigest()
        index_name = f"asof_trgm_{table_hash}_{column}_{fields_hash}"
        expressions = []
        for field in unique_fields:
            path = ", ".join(
                "'" + part.replace("'", "''") + "'" for part in field.split(".")
            )
            expressions.append(
                f'lower(jsonb_extract_path_text("{column}", {path})) gin_trgm_ops'
            )
        self._execute(
            f'CREATE INDEX IF NOT EXISTS "{index_name}" '
            f'ON {self._quoted_table} USING GIN ({", ".join(expressions)})'
        )
        self._connection.commit()
        return [index_name]

    def put(self, as_of: Timestamp, key: Key, value: Value) -> bool:
        encoded_timestamp = _database_value(
            self._timestamp_type, as_of, postgres=self._postgres
        )
        encoded_key = _database_value(self._key_type, key, postgres=self._postgres)
        key_parameter = self._parameter(self._key_type)
        timestamp_parameter = self._parameter(self._timestamp_type)
        value_column = "" if self._value_type is type(None) else ', "value"'
        value_parameter = (
            ""
            if self._value_type is type(None)
            else ", " + self._parameter(self._value_type, 3)
        )
        latest_columns = self._timestamp_column() + (
            "" if self._value_type is type(None) else ', "value"'
        )
        latest_order = (
            ""
            if self._timestamp_type is Decimal
            else ' ORDER BY "timestamp" DESC LIMIT 1'
        )
        latest = self._read(
            f'SELECT {latest_columns} FROM {self._quoted_table} '
            f'WHERE "key" = {key_parameter}{latest_order}',
            (encoded_key,),
        )
        if not latest.is_empty():
            if self._timestamp_type is Decimal:
                latest_row = max(
                    latest.iter_rows(named=True),
                    key=lambda row: _decode(
                        self._timestamp_type, row["timestamp"]
                    ),
                )
                latest_timestamp = _decode(
                    self._timestamp_type, latest_row["timestamp"]
                )
                latest_value = (
                    None
                    if self._value_type is type(None)
                    else _decode(self._value_type, latest_row["value"])
                )
            else:
                latest_timestamp = _decode(
                    self._timestamp_type, latest.item(0, "timestamp")
                )
                latest_value = (
                    None
                    if self._value_type is type(None)
                    else _decode(self._value_type, latest.item(0, "value"))
                )
            if as_of >= latest_timestamp and value == latest_value:
                return False
            if as_of <= latest_timestamp:
                raise ValueError(
                    f"timestamp {as_of!r} must be greater than the latest "
                    f"timestamp {latest_timestamp!r} for key {key!r}"
                )

        parameters: tuple[Any, ...] = (
            encoded_timestamp,
            encoded_key,
        )
        insert_columns = '"timestamp", "key"'
        insert_values = (
            f"{timestamp_parameter}, {self._parameter(self._key_type, 2)}"
        )
        if self._value_type is not type(None):
            insert_columns += value_column
            insert_values += value_parameter
            parameters += (
                _database_value(self._value_type, value, postgres=self._postgres),
            )
        self._execute(
            f"INSERT INTO {self._quoted_table} ({insert_columns}) "
            f"VALUES ({insert_values})",
            parameters,
        )
        self._connection.commit()
        return True

    def get(self, as_of: Timestamp | None, key: Key) -> Value | None:
        columns = (
            self._timestamp_column()
            if self._value_type is type(None)
            else '"value"'
        )
        conditions = [f'"key" = {self._parameter(self._key_type)}']
        parameters: tuple[Any, ...] = (
            _database_value(self._key_type, key, postgres=self._postgres),
        )
        if self._timestamp_type is Decimal:
            key_condition = f'"key" = {self._parameter(self._key_type)}'
            matches = self._read(
                f'SELECT "timestamp", {columns} FROM {self._quoted_table} '
                f"WHERE {key_condition}",
                parameters,
            )
            eligible = [
                row
                for row in matches.iter_rows(named=True)
                if as_of is None
                or _decode(self._timestamp_type, row["timestamp"]) <= as_of
            ]
            if not eligible or self._value_type is type(None):
                return None
            latest = max(
                eligible, key=lambda row: _decode(self._timestamp_type, row["timestamp"])
            )
            return _decode(self._value_type, latest["value"])
        if as_of is not None and self._timestamp_type is not Decimal:
            conditions.append(
                f'"timestamp" <= {self._parameter(self._timestamp_type, 2)}'
            )
            parameters += (
                _database_value(self._timestamp_type, as_of, postgres=self._postgres),
            )
        result = self._read(
            f"SELECT {columns} FROM {self._quoted_table} "
            f"WHERE {' AND '.join(conditions)} ORDER BY \"timestamp\" DESC LIMIT 1",
            parameters,
        )
        if result.is_empty() or self._value_type is type(None):
            return None
        return _decode(self._value_type, result.item(0, "value"))

    def _latest_per_key(self, as_of: Timestamp | None = None) -> list[dict[str, Any]]:
        columns = f'{self._timestamp_column()}, "key"'
        if self._value_type is not type(None):
            columns += ', "value"'
        where = ""
        parameters: tuple[Any, ...] = ()
        if as_of is not None and self._timestamp_type is not Decimal:
            where = (
                f' WHERE "timestamp" <= '
                f'{self._parameter(self._timestamp_type)}'
            )
            parameters = (
                _database_value(self._timestamp_type, as_of, postgres=self._postgres),
            )
        timestamp_order = (
            "" if self._timestamp_type is Decimal else ', "timestamp" DESC'
        )
        rows = self._read(
            f"SELECT {columns} FROM {self._quoted_table}{where} "
            f'ORDER BY "key"{timestamp_order}',
            parameters,
        )
        if self._timestamp_type is Decimal:
            latest: dict[str, dict[str, Any]] = {}
            for row in rows.iter_rows(named=True):
                timestamp = _decode(self._timestamp_type, row["timestamp"])
                if as_of is not None and timestamp > as_of:
                    continue
                key = _decode(self._key_type, row["key"])
                stable_key = json.dumps(
                    _encode(self._key_type, key),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                current = latest.get(stable_key)
                if current is None or timestamp > current["timestamp"]:
                    record = {"timestamp": timestamp, "key": key}
                    if self._value_type is not type(None):
                        record["value"] = _decode(self._value_type, row["value"])
                    latest[stable_key] = record
            return list(latest.values())

        if rows.is_empty():
            return []
        return [
            {
                "timestamp": _decode(self._timestamp_type, row["timestamp"]),
                "key": _decode(self._key_type, row["key"]),
                **(
                    {"value": _decode(self._value_type, row["value"])}
                    if self._value_type is not type(None)
                    else {}
                ),
            }
            for row in rows.unique(
                subset=["key"], keep="first", maintain_order=True
            ).iter_rows(named=True)
        ]

    def get_all(self, as_of: Timestamp | None = None) -> list[Value | None]:
        records = self._latest_per_key(as_of)
        if self._value_type is type(None):
            return [None for _ in records]
        return [record["value"] for record in records]

    def get_all_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Value | None]:
        if not (self._value_type is dict or _is_pydantic_model(self._value_type)):
            raise TypeError("get_all_matching requires dict or Pydantic model values")
        if not filters:
            return self.get_all(as_of)
        return [
            value
            for value in self.get_all(as_of)
            if value is not None and _matches_fields(value, filters, exact=exact)
        ]

    def get_all_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Value | None]:
        if not (self._value_type is dict or _is_pydantic_model(self._value_type)):
            raise TypeError(
                "get_all_matching_terms requires dict or Pydantic model values"
            )
        if not terms:
            return self.get_all(as_of)
        if self._postgres:
            matches = self._get_postgres_matching_json_terms(
                terms, fields, as_of, column="value", exact=exact
            )
            if matches is not None:
                return [
                    value
                    for value in matches
                    if value is not None
                    and _matches_terms(value, terms, fields, exact=exact)
                ]
        return [
            value
            for value in self.get_all(as_of)
            if value is not None
            and _matches_terms(value, terms, fields, exact=exact)
        ]

    def get_keys_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Key]:
        if not (self._key_type is dict or _is_pydantic_model(self._key_type)):
            raise TypeError("get_keys_matching requires dict or Pydantic model keys")
        keys = [record["key"] for record in self._latest_per_key(as_of)]
        return [
            key
            for key in keys
            if _matches_fields(key, filters, exact=exact)
        ]

    def get_keys_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Key]:
        if not (self._key_type is dict or _is_pydantic_model(self._key_type)):
            raise TypeError(
                "get_keys_matching_terms requires dict or Pydantic model keys"
            )
        if not terms:
            return [record["key"] for record in self._latest_per_key(as_of)]
        if self._postgres:
            matches = self._get_postgres_matching_json_terms(
                terms, fields, as_of, column="key", exact=exact
            )
            if matches is not None:
                return [
                    key
                    for key in matches
                    if _matches_terms(key, terms, fields, exact=exact)
                ]
        keys = [record["key"] for record in self._latest_per_key(as_of)]
        return [
            key
            for key in keys
            if _matches_terms(key, terms, fields, exact=exact)
        ]

    def _get_postgres_matching_json_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        as_of: Timestamp | None,
        *,
        column: str,
        exact: bool,
    ) -> list[Any] | None:
        if (
            self._timestamp_type is Decimal
            or not all(
                isinstance(term, str) and term and term.isascii() for term in terms
            )
        ):
            return None
        if not fields or any(
            not isinstance(field, str)
            or not field
            or any(
                not part
                or not (part[0].isalpha() or part[0] == "_")
                or not all(char.isalnum() or char == "_" for char in part)
                for part in field.split(".")
            )
            for field in fields
        ):
            return None

        parameters: list[Any] = []
        term_predicates: list[str] = []
        for term in terms:
            field_predicates: list[str] = []
            for field in fields:
                path = field.split(".")
                path_literal = ", ".join(
                    "'" + part.replace("'", "''") + "'" for part in path
                )
                type_value = f'jsonb_extract_path("{column}", {path_literal})'
                text_value = f'jsonb_extract_path_text("{column}", {path_literal})'
                term_parameter = self._parameter(str, len(parameters) + 1)
                escaped_term = (
                    term.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                pattern = escaped_term if exact else f"%{escaped_term}%"
                parameters.append(pattern.lower())
                match = f"lower({text_value}) LIKE {term_parameter}"
                field_predicates.append(
                    f"(jsonb_typeof({type_value}) "
                    f"IN ('string', 'number', 'boolean') AND {match})"
                )
            term_predicates.append(f"({' OR '.join(field_predicates)})")

        timestamp_filter = ""
        newer_timestamp_filter = ""
        if as_of is not None:
            timestamp_parameter = self._parameter(
                self._timestamp_type, len(parameters) + 1
            )
            timestamp_filter = f't."timestamp" <= {timestamp_parameter}'
            parameters.append(
                _database_value(
                    self._timestamp_type, as_of, postgres=self._postgres
                )
            )
            newer_timestamp_parameter = self._parameter(
                self._timestamp_type, len(parameters) + 1
            )
            newer_timestamp_filter = (
                f' AND newer."timestamp" <= {newer_timestamp_parameter}'
            )
            parameters.append(
                _database_value(
                    self._timestamp_type, as_of, postgres=self._postgres
                )
            )

        rows = self._read(
            f'SELECT t."{column}" FROM {self._quoted_table} AS t '
            f'WHERE {" AND ".join(term_predicates)} '
            f'AND {timestamp_filter or "TRUE"} '
            f'AND NOT EXISTS (SELECT 1 FROM {self._quoted_table} AS newer '
            f'WHERE newer."key" = t."key" '
            f'AND newer."timestamp" > t."timestamp"{newer_timestamp_filter}) '
            f'ORDER BY t."key"',
            parameters,
        )
        python_type = self._value_type if column == "value" else self._key_type
        return [
            _decode(python_type, value)
            for value in rows.get_column(column).to_list()
        ]


__all__ = ["SqlBackend"]
