import pickle
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    create_engine,
    select,
    update,
)
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


class SqlBackend[Timestamp: SupportsAllComparisons, Key, Value]:
    def __init__(self, sql_uri: str) -> None:
        self._engine: Engine = create_engine(sql_uri)
        metadata = MetaData()
        self._versions = Table(
            "asof_store_versions",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("key_data", LargeBinary, nullable=False),
            Column("timestamp_data", LargeBinary, nullable=False),
            Column("value_data", LargeBinary, nullable=False),
        )
        Index("ix_asof_store_versions_key", self._versions.c.key_data)
        metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def put(self, as_of: Timestamp, key: Key, value: Value) -> None:
        key_data = pickle.dumps(key, protocol=pickle.HIGHEST_PROTOCOL)
        timestamp_data = pickle.dumps(as_of, protocol=pickle.HIGHEST_PROTOCOL)
        value_data = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

        with self._engine.begin() as connection:
            rows = connection.execute(
                select(self._versions.c.id, self._versions.c.timestamp_data).where(
                    self._versions.c.key_data == key_data
                )
            )
            for row in rows:
                if pickle.loads(row.timestamp_data) == as_of:
                    connection.execute(
                        update(self._versions)
                        .where(self._versions.c.id == row.id)
                        .values(value_data=value_data)
                    )
                    return

            connection.execute(
                self._versions.insert().values(
                    key_data=key_data,
                    timestamp_data=timestamp_data,
                    value_data=value_data,
                )
            )

    def get(self, as_of: Timestamp, key: Key) -> Value | None:
        key_data = pickle.dumps(key, protocol=pickle.HIGHEST_PROTOCOL)
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    self._versions.c.timestamp_data, self._versions.c.value_data
                ).where(self._versions.c.key_data == key_data)
            )
            latest_timestamp: Timestamp | None = None
            latest_value: Value | None = None
            for row in rows:
                timestamp: Timestamp = pickle.loads(row.timestamp_data)
                if timestamp <= as_of and (
                    latest_timestamp is None or timestamp > latest_timestamp
                ):
                    latest_timestamp = timestamp
                    latest_value = pickle.loads(row.value_data)
            return latest_value
