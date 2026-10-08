from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons

    from ._memory import MemoryBackend
    from ._sql import SqlBackend


class AsOfStore[Timestamp: SupportsAllComparisons, Key, Value]:
    """Factory for versioned store backends."""

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


__all__ = ["AsOfStore"]
