import pickle
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    LargeBinary,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    Uuid,
    create_engine,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.selectable import Subquery
from sqlalchemy.sql.type_api import TypeEngine

from ._abc import AsOfStoreABC

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


def _sql_type(python_type: type[Any]) -> TypeEngine[Any]:
    if python_type is bool:
        return Boolean()
    if python_type is int:
        return BigInteger()
    if python_type is float:
        return Float()
    if python_type is str:
        return Text()
    if python_type is bytes:
        return LargeBinary()
    if python_type is datetime:
        return DateTime(timezone=True)
    if python_type is date:
        return Date()
    if python_type is Decimal:
        return Numeric()
    if python_type is UUID:
        return Uuid(as_uuid=True)
    if _uses_json(python_type):
        return JSON().with_variant(JSONB, "postgresql")
    return LargeBinary()


def _json_terms_conditions(
    column: Any,
    terms: Sequence[str],
    fields: Sequence[str],
    *,
    exact: bool,
) -> list[Any]:
    if not terms:
        return []
    if not fields:
        return [False]
    expressions = []
    for field in fields:
        if not isinstance(field, str) or not field:
            raise ValueError("field names must be non-empty strings")
        expression = column
        for part in field.split("."):
            if not part:
                raise ValueError(f"invalid dotted field name: {field!r}")
            expression = expression[part]
        expressions.append(func.lower(expression.as_string()))

    conditions = []
    for term in terms:
        if not isinstance(term, str) or not term:
            raise ValueError("search terms must be non-empty strings")
        normalized_term = term.casefold()
        matches = [
            expression == normalized_term
            if exact
            else expression.contains(normalized_term, autoescape=True)
            for expression in expressions
        ]
        conditions.append(or_(*matches))
    return conditions


def _uses_json(python_type: type[Any]) -> bool:
    return python_type in (dict, list, tuple) or _is_pydantic_model(python_type)


def _is_pydantic_model(python_type: type[Any]) -> bool:
    try:
        from pydantic import BaseModel
    except ImportError:
        return False
    return issubclass(python_type, BaseModel)


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
    if _is_pydantic_model(python_type):
        return python_type.model_validate(value)
    if python_type is tuple:
        return tuple(value)
    if python_type is datetime and value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


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

        for name, python_type in (
            ("timestamp_type", timestamp_type),
            ("key_type", key_type),
            ("value_type", value_type),
        ):
            if not isinstance(python_type, type):
                raise TypeError(f"{name} must be a Python type")

        if timestamp_type not in _SORTABLE_TIMESTAMP_TYPES:
            supported_types = ", ".join(
                sorted(
                    python_type.__name__ for python_type in _SORTABLE_TIMESTAMP_TYPES
                )
            )
            raise TypeError(
                f"timestamp_type must map to an indexable, sortable SQL scalar "
                f"type ({supported_types}); got {timestamp_type.__name__}"
            )

        self._engine = create_engine(sql_uri)
        if len(table_name) > self._engine.dialect.max_identifier_length:
            self._engine.dispose()
            raise ValueError(
                f"table_name exceeds the dialect limit of "
                f"{self._engine.dialect.max_identifier_length} characters"
            )

        self._timestamp_type = timestamp_type
        self._key_type = key_type
        self._value_type = value_type
        metadata = MetaData()
        columns = [
            Column(
                "timestamp",
                _sql_type(timestamp_type),
                nullable=False,
            ),
            Column(
                "key",
                _sql_type(key_type),
                nullable=False,
            ),
        ]
        if value_type is not type(None):
            columns.append(
                Column(
                    "value",
                    _sql_type(value_type),
                    nullable=True,
                )
            )
        columns.append(PrimaryKeyConstraint("key", "timestamp"))
        self._versions = Table(table_name, metadata, *columns)
        metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def put(self, as_of: Timestamp, key: Key, value: Value) -> bool:
        timestamp = _encode(self._timestamp_type, as_of)
        encoded_key = _encode(self._key_type, key)
        encoded_value = _encode(self._value_type, value)
        with self._engine.begin() as connection:
            latest_columns = [self._versions.c.timestamp]
            if self._value_type is not type(None):
                latest_columns.append(self._versions.c.value)
            latest = connection.execute(
                select(*latest_columns)
                .where(self._versions.c.key == encoded_key)
                .order_by(self._versions.c.timestamp.desc())
                .limit(1)
            ).first()

            if latest is not None:
                latest_timestamp = _decode(self._timestamp_type, latest.timestamp)
                latest_value = (
                    None
                    if self._value_type is type(None)
                    else _decode(self._value_type, latest.value)
                )
                if as_of >= latest_timestamp and value == latest_value:
                    return False
                if as_of <= latest_timestamp:
                    raise ValueError(
                        f"timestamp {as_of!r} must be greater than the latest "
                        f"timestamp {latest_timestamp!r} for key {key!r}"
                    )

            insert_values = {"timestamp": timestamp, "key": encoded_key}
            if self._value_type is not type(None):
                insert_values["value"] = encoded_value
            connection.execute(self._versions.insert().values(**insert_values))
            return True

    def get(self, as_of: Timestamp | None, key: Key) -> Value | None:
        encoded_key = _encode(self._key_type, key)
        conditions = [self._versions.c.key == encoded_key]
        if as_of is not None:
            timestamp = _encode(self._timestamp_type, as_of)
            conditions.append(self._versions.c.timestamp <= timestamp)
        result_column = (
            self._versions.c.timestamp
            if self._value_type is type(None)
            else self._versions.c.value
        )
        with self._engine.connect() as connection:
            value = connection.scalar(
                select(result_column)
                .where(*conditions)
                .order_by(self._versions.c.timestamp.desc())
                .limit(1)
            )
        if self._value_type is type(None):
            return None
        return _decode(self._value_type, value)

    def _latest_per_key(self, as_of: Timestamp | None = None) -> Subquery:
        value_column = (
            self._versions.c.timestamp
            if self._value_type is type(None)
            else self._versions.c.value
        )
        conditions = (
            [self._versions.c.timestamp <= _encode(self._timestamp_type, as_of)]
            if as_of is not None
            else []
        )
        return (
            select(
                self._versions.c.key,
                value_column.label("value"),
                func.row_number()
                .over(
                    partition_by=self._versions.c.key,
                    order_by=self._versions.c.timestamp.desc(),
                )
                .label("row_number"),
            )
            .where(*conditions)
            .subquery()
        )

    def get_all(self, as_of: Timestamp | None = None) -> list[Value | None]:
        """Return each key's latest value at or before as_of."""
        latest = self._latest_per_key(as_of)
        statement = (
            select(latest.c.value)
            .where(latest.c.row_number == 1)
            .order_by(latest.c.key)
        )
        with self._engine.connect() as connection:
            values = connection.scalars(statement).all()

        if self._value_type is type(None):
            return [None for _ in values]
        return [_decode(self._value_type, value) for value in values]

    def get_all_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Value | None]:
        """Filter latest JSON values in SQL before decoding them."""
        if not (self._value_type is dict or _is_pydantic_model(self._value_type)):
            raise TypeError("get_all_matching requires dict or Pydantic model values")
        if not filters:
            return self.get_all(as_of)

        latest = self._latest_per_key(as_of)
        conditions = [latest.c.row_number == 1]
        filter_items = filters.items() if isinstance(filters, Mapping) else filters
        for field, query in filter_items:
            if not isinstance(field, str) or not field:
                raise ValueError("filter field names must be non-empty strings")
            if not isinstance(query, str):
                raise TypeError("filter values must be strings")
            expression = latest.c.value
            for part in field.split("."):
                if not part:
                    raise ValueError(f"invalid dotted field name: {field!r}")
                expression = expression[part]
            text_value = func.lower(expression.as_string())
            normalized_query = query.casefold()
            conditions.append(
                text_value == normalized_query
                if exact
                else text_value.contains(normalized_query, autoescape=True)
            )

        statement = select(latest.c.value).where(*conditions).order_by(latest.c.key)
        with self._engine.connect() as connection:
            values = connection.scalars(statement).all()
        return [_decode(self._value_type, value) for value in values]

    def get_all_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Value | None]:
        """Match every term against at least one JSON value field in SQL."""
        if not (self._value_type is dict or _is_pydantic_model(self._value_type)):
            raise TypeError(
                "get_all_matching_terms requires dict or Pydantic model values"
            )
        if not terms:
            return self.get_all(as_of)
        latest = self._latest_per_key(as_of)
        conditions = [latest.c.row_number == 1]
        conditions.extend(
            _json_terms_conditions(latest.c.value, terms, fields, exact=exact)
        )
        statement = select(latest.c.value).where(*conditions).order_by(latest.c.key)
        with self._engine.connect() as connection:
            values = connection.scalars(statement).all()
        return [_decode(self._value_type, value) for value in values]

    def get_keys_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Key]:
        """Filter JSON keys in SQL and decode only the matching keys."""
        if not (self._key_type is dict or _is_pydantic_model(self._key_type)):
            raise TypeError("get_keys_matching requires dict or Pydantic model keys")
        if not filters:
            latest = self._latest_per_key(as_of)
            statement = (
                select(latest.c.key)
                .where(latest.c.row_number == 1)
                .order_by(latest.c.key)
            )
        else:
            latest = self._latest_per_key(as_of)
            conditions = [latest.c.row_number == 1]
            filter_items = filters.items() if isinstance(filters, Mapping) else filters
            for field, query in filter_items:
                if not isinstance(field, str) or not field:
                    raise ValueError("filter field names must be non-empty strings")
                if not isinstance(query, str):
                    raise TypeError("filter values must be strings")
                expression = latest.c.key
                for part in field.split("."):
                    if not part:
                        raise ValueError(f"invalid dotted field name: {field!r}")
                    expression = expression[part]
                text_value = func.lower(expression.as_string())
                normalized_query = query.casefold()
                conditions.append(
                    text_value == normalized_query
                    if exact
                    else text_value.contains(normalized_query, autoescape=True)
                )
            statement = select(latest.c.key).where(*conditions).order_by(latest.c.key)
        with self._engine.connect() as connection:
            values = connection.scalars(statement).all()
        return [_decode(self._key_type, value) for value in values]

    def get_keys_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Key]:
        """Match every term against at least one JSON key field in SQL."""
        if not (self._key_type is dict or _is_pydantic_model(self._key_type)):
            raise TypeError(
                "get_keys_matching_terms requires dict or Pydantic model keys"
            )
        latest = self._latest_per_key(as_of)
        conditions = [latest.c.row_number == 1]
        conditions.extend(
            _json_terms_conditions(latest.c.key, terms, fields, exact=exact)
        )
        statement = select(latest.c.key).where(*conditions).order_by(latest.c.key)
        with self._engine.connect() as connection:
            values = connection.scalars(statement).all()
        return [_decode(self._key_type, value) for value in values]

    def get_unique_set(
        self, field: str, as_of: Timestamp | None = None
    ) -> set[str | None]:
        if not _uses_json(self._value_type):
            raise TypeError("get_unique_set requires a JSON-compatible value_type")
        return super().get_unique_set(field, as_of)
