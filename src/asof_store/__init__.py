from abc import ABC, abstractmethod
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons

    from ._memory import MemoryBackend
    from ._sql import SqlBackend


class AsOfStore[Timestamp: SupportsAllComparisons, Key, Value](ABC):
    """Factory and interface for versioned stores."""

    @classmethod
    def from_memory(cls) -> MemoryBackend[Timestamp, Key, Value]:
        from ._memory import MemoryBackend

        return MemoryBackend[Timestamp, Key, Value]()

    @classmethod
    def from_sql(
        cls,
        sql_uri: str,
        table_name: str,
        timestamp_type: type[Timestamp],
        key_type: type[Key],
        value_type: type[Value],
    ) -> SqlBackend[Timestamp, Key, Value]:
        """Create a SQL-backed store using the provided value types."""
        try:
            from ._sql import SqlBackend
        except ImportError as exc:
            raise ImportError(
                "SQL storage requires optional dependencies; install "
                "'asof-store[sql-sqlite]' for SQLite or "
                "'asof-store[sql-postgres]' for PostgreSQL"
            ) from exc

        return SqlBackend[Timestamp, Key, Value](
            sql_uri,
            table_name,
            timestamp_type,
            key_type,
            value_type,
        )

    @abstractmethod
    def put(self, as_of: Timestamp, key: Key, value: Value) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, as_of: Timestamp, key: Key) -> Value | None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release resources held by the store."""
        raise NotImplementedError

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
