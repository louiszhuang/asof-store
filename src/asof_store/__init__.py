from bisect import bisect_left, bisect_right
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


class _StoreBackend[Timestamp, Key, Value](Protocol):
    def put(self, as_of: Timestamp, key: Key, value: Value) -> None: ...

    def get(self, as_of: Timestamp, key: Key) -> Value | None: ...

    def close(self) -> None: ...


class AsOfStore[Timestamp: SupportsAllComparisons, Key, Value]:
    """An in-memory store for values indexed by key and timestamp."""

    def __init__(self) -> None:
        self._versions: dict[Key, tuple[list[Timestamp], list[Value]]] = {}
        self._sql_backend: _StoreBackend[Timestamp, Key, Value] | None = None

    @classmethod
    def from_memory(cls) -> AsOfStore[Timestamp, Key, Value]:
        return cls()

    @classmethod
    def from_sql(
        cls,
        sql_uri: str,
        table_name: str,
        timestamp_type: type[Timestamp],
        key_type: type[Key],
        value_type: type[Value],
    ) -> AsOfStore[Timestamp, Key, Value]:
        """Create a SQL-backed store using the provided value types."""
        try:
            from ._sql import SqlBackend
        except ImportError as exc:
            raise ImportError(
                "SQL storage requires optional dependencies; install "
                "'asof-store[sql-sqlite]' for SQLite or "
                "'asof-store[sql-postgres]' for PostgreSQL"
            ) from exc

        store = cls()
        store._sql_backend = SqlBackend[Timestamp, Key, Value](
            sql_uri,
            table_name,
            timestamp_type,
            key_type,
            value_type,
        )
        return store

    def put(self, as_of: Timestamp, key: Key, value: Value) -> None:
        if self._sql_backend is not None:
            self._sql_backend.put(as_of, key, value)
            return

        timestamps, values = self._versions.setdefault(key, ([], []))
        index = bisect_left(timestamps, as_of)
        if index < len(timestamps) and timestamps[index] == as_of:
            values[index] = value
            return

        timestamps.insert(index, as_of)
        values.insert(index, value)

    def get(self, as_of: Timestamp, key: Key) -> Value | None:
        if self._sql_backend is not None:
            return self._sql_backend.get(as_of, key)

        versions = self._versions.get(key)
        if versions is None:
            return None

        timestamps, values = versions
        index = bisect_right(timestamps, as_of) - 1
        if index < 0:
            return None
        return values[index]

    def close(self) -> None:
        """Release resources held by an optional SQL backend."""
        if self._sql_backend is not None:
            self._sql_backend.close()

    def as_of(self, as_of: Timestamp) -> AsOfView[Timestamp, Key, Value]:
        return AsOfView(self, as_of)


class AsOfView[Timestamp: SupportsAllComparisons, Key, Value]:
    """A view of an AsOfStore queried at one fixed timestamp."""

    def __init__(self, store: AsOfStore[Timestamp, Key, Value], as_of: Timestamp):
        self._store = store
        self._as_of = as_of

    def get(self, key: Key) -> Value | None:
        return self._store.get(self._as_of, key)

    def __enter__(self) -> AsOfView[Timestamp, Key, Value]:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def main() -> None:
    print("Hello from asof-store!")


__all__ = ["AsOfStore", "AsOfView", "main"]
