from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING

from . import AsOfStore

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


class MemoryBackend[Timestamp: SupportsAllComparisons, Key, Value](
    AsOfStore[Timestamp, Key, Value]
):
    """In-memory implementation of the AsOfStore interface."""

    def __init__(self) -> None:
        self._versions: dict[Key, tuple[list[Timestamp], list[Value]]] = {}

    def put(self, as_of: Timestamp, key: Key, value: Value) -> None:
        timestamps, values = self._versions.setdefault(key, ([], []))
        index = bisect_left(timestamps, as_of)
        if index < len(timestamps) and timestamps[index] == as_of:
            values[index] = value
            return

        timestamps.insert(index, as_of)
        values.insert(index, value)

    def get(self, as_of: Timestamp, key: Key) -> Value | None:
        versions = self._versions.get(key)
        if versions is None:
            return None

        timestamps, values = versions
        index = bisect_right(timestamps, as_of) - 1
        if index < 0:
            return None
        return values[index]

    def close(self) -> None:
        return None
