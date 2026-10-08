import pickle
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
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    if python_type in (dict, list) or _is_pydantic_model(python_type):
        return JSON().with_variant(JSONB, "postgresql")
    return LargeBinary()


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
        self._versions = Table(
            table_name,
            metadata,
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
            PrimaryKeyConstraint("key", "timestamp"),
        )
        metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def put(self, as_of: Timestamp, key: Key, value: Value) -> bool:
        timestamp = _encode(self._timestamp_type, as_of)
        encoded_key = _encode(self._key_type, key)
        encoded_value = _encode(self._value_type, value)
        with self._engine.begin() as connection:
            latest = connection.execute(
                select(self._versions.c.timestamp, self._versions.c.value)
                .where(self._versions.c.key == encoded_key)
                .order_by(self._versions.c.timestamp.desc())
                .limit(1)
            ).first()

            if latest is not None:
                latest_timestamp = _decode(self._timestamp_type, latest.timestamp)
                latest_value = _decode(self._value_type, latest.value)
                if as_of >= latest_timestamp and value == latest_value:
                    return False
                if as_of <= latest_timestamp:
                    raise ValueError(
                        f"timestamp {as_of!r} must be greater than the latest "
                        f"timestamp {latest_timestamp!r} for key {key!r}"
                    )

            connection.execute(
                self._versions.insert().values(
                    timestamp=timestamp,
                    key=encoded_key,
                    value=encoded_value,
                )
            )
            return True

    def get(self, as_of: Timestamp, key: Key) -> Value | None:
        timestamp = _encode(self._timestamp_type, as_of)
        encoded_key = _encode(self._key_type, key)
        with self._engine.connect() as connection:
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
