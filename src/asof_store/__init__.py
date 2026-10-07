from bisect import bisect_left, bisect_right
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import SupportsRichComparison


class AsOfStore[Timestamp: SupportsRichComparison, Key, Value]:
    """An in-memory store for values indexed by key and timestamp."""

    def __init__(self) -> None:
        self._versions: dict[Key, tuple[list[Timestamp], list[Value]]] = {}

    @classmethod
    def from_memory(cls) -> AsOfStore[Timestamp, Key, Value]:
        return cls()

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

    def as_of(self, as_of: Timestamp) -> AsOfView[Timestamp, Key, Value]:
        return AsOfView(self, as_of)


class AsOfView[Timestamp: SupportsRichComparison, Key, Value]:
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
