import pickle
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
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
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    Table,
    Text,
    Uuid,
    create_engine,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.sql.type_api import TypeEngine

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


def _create_engine(sql_uri: str) -> Engine:
    return create_engine(sql_uri)


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
    if python_type in (dict, list):
        return JSON().with_variant(JSONB, "postgresql")
    return LargeBinary()


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
    }
    return python_type not in native_types


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
    if python_type is datetime and value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


class SqlBackend[Timestamp: SupportsAllComparisons, Key, Value]:
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

        self._engine = _create_engine(sql_uri)
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
        self._versions = Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
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
            Column(
                "value",
                _sql_type(value_type),
                nullable=True,
            ),
        )
        index_suffix = sha256(table_name.encode()).hexdigest()[:16]
        Index(f"ix_{index_suffix}_key", self._versions.c.key)
        metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def put(self, as_of: Timestamp, key: Key, value: Value) -> None:
        timestamp = _encode(self._timestamp_type, as_of)
        encoded_key = _encode(self._key_type, key)
        encoded_value = _encode(self._value_type, value)
        with self._engine.begin() as connection:
            rows = connection.execute(
                select(self._versions.c.id, self._versions.c.timestamp).where(
                    self._versions.c.key == encoded_key
                )
            )
            row_id = next(
                (
                    row.id
                    for row in rows
                    if _decode(self._timestamp_type, row.timestamp) == as_of
                ),
                None,
            )
            if row_id is None:
                connection.execute(
                    self._versions.insert().values(
                        timestamp=timestamp,
                        key=encoded_key,
                        value=encoded_value,
                    )
                )
            else:
                connection.execute(
                    update(self._versions)
                    .where(self._versions.c.id == row_id)
                    .values(value=encoded_value)
                )

    def get(self, as_of: Timestamp, key: Key) -> Value | None:
        timestamp = _encode(self._timestamp_type, as_of)
        encoded_key = _encode(self._key_type, key)
        with self._engine.connect() as connection:
            if _uses_pickle(self._timestamp_type):
                rows = connection.execute(
                    select(
                        self._versions.c.timestamp,
                        self._versions.c.value,
                    ).where(self._versions.c.key == encoded_key)
                )
                latest_timestamp: Timestamp | None = None
                value = None
                for row in rows:
                    stored_timestamp: Timestamp = _decode(
                        self._timestamp_type, row.timestamp
                    )
                    if stored_timestamp <= as_of and (
                        latest_timestamp is None or stored_timestamp > latest_timestamp
                    ):
                        latest_timestamp = stored_timestamp
                        value = row.value
            else:
                value = connection.scalar(
                    select(self._versions.c.value)
                    .where(
                        self._versions.c.key == encoded_key,
                        self._versions.c.timestamp <= timestamp,
                    )
                    .order_by(self._versions.c.timestamp.desc())
                    .limit(1)
                )
        return _decode(self._value_type, value)
