from bisect import bisect_right
from typing import TYPE_CHECKING

from ._abc import AsOfStoreABC

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


class MemoryBackend[Timestamp: SupportsAllComparisons, Key, Value](
    AsOfStoreABC[Timestamp, Key, Value]
):
    """In-memory implementation of the AsOfStore interface."""

    def __init__(self) -> None:
        self._versions: dict[Key, tuple[list[Timestamp], list[Value]]] = {}
        self._unhashable_versions: list[
            tuple[Key, tuple[list[Timestamp], list[Value]]]
        ] = []

    def _get_versions(self, key: Key) -> tuple[list[Timestamp], list[Value]] | None:
        try:
            hash(key)
        except TypeError:
            return next(
                (
                    versions
                    for stored_key, versions in self._unhashable_versions
                    if stored_key == key
                ),
                None,
            )
        return self._versions.get(key)

    def put(self, as_of: Timestamp, key: Key, value: Value) -> bool:
        versions = self._get_versions(key)
        if versions is None:
            try:
                hash(key)
            except TypeError:
                self._unhashable_versions.append((key, ([as_of], [value])))
            else:
                self._versions[key] = ([as_of], [value])
            return True

        timestamps, values = versions
        latest_timestamp = timestamps[-1]
        if as_of == latest_timestamp and value == values[-1]:
            return False
        if as_of <= latest_timestamp:
            raise ValueError(
                f"timestamp {as_of!r} must be greater than the latest "
                f"timestamp {latest_timestamp!r} for key {key!r}"
            )
        if value == values[-1]:
            return False

        timestamps.append(as_of)
        values.append(value)
        return True

    def get(self, as_of: Timestamp, key: Key) -> Value | None:
        versions = self._get_versions(key)
        if versions is None:
            return None

        timestamps, values = versions
        index = bisect_right(timestamps, as_of) - 1
        if index < 0:
            return None
        return values[index]

    def close(self) -> None:
        return None
