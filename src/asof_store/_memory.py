from bisect import bisect_right
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ._abc import AsOfStore, _matches_fields, _matches_terms

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


class MemoryBackend[Timestamp: SupportsAllComparisons, Key, Value](
    AsOfStore[Timestamp, Key, Value]
):
    """In-memory implementation of the AsOfStore interface."""

    def __init__(self) -> None:
        self._versions: dict[
            Key, tuple[list[Timestamp], list[Value | None] | None]
        ] = {}

    def put(self, as_of: Timestamp, key: Key, value: Value) -> bool:
        versions = self._versions.get(key)
        if versions is None:
            self._versions[key] = ([as_of], [value] if value is not None else None)
            return True

        timestamps, values = versions
        latest_timestamp = timestamps[-1]
        latest_value = None if values is None else values[-1]
        if as_of == latest_timestamp and value == latest_value:
            return False
        if as_of <= latest_timestamp:
            raise ValueError(
                f"timestamp {as_of!r} must be greater than the latest "
                f"timestamp {latest_timestamp!r} for key {key!r}"
            )
        if value == latest_value:
            return False

        if values is None:
            values = [None] * len(timestamps)
            values.append(value)
            self._versions[key] = (timestamps + [as_of], values)
            return True

        timestamps.append(as_of)
        values.append(value)
        return True

    def get(self, as_of: Timestamp | None, key: Key) -> Value | None:
        versions = self._versions.get(key)
        if versions is None:
            return None

        timestamps, values = versions
        index = (
            len(timestamps) - 1
            if as_of is None
            else bisect_right(timestamps, as_of) - 1
        )
        if index < 0 or values is None:
            return None
        return values[index]

    def get_all(self, as_of: Timestamp | None = None) -> list[Value | None]:
        values: list[Value | None] = []
        for timestamps, versions in self._versions.values():
            index = (
                len(timestamps) - 1
                if as_of is None
                else bisect_right(timestamps, as_of) - 1
            )
            if index >= 0:
                values.append(None if versions is None else versions[index])
        return values

    def get_keys_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Key]:
        keys: list[Key] = []
        for key, (timestamps, _) in self._versions.items():
            index = (
                len(timestamps) - 1
                if as_of is None
                else bisect_right(timestamps, as_of) - 1
            )
            if index >= 0 and _matches_fields(key, filters, exact=exact):
                keys.append(key)
        return keys

    def get_keys_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Key]:
        keys: list[Key] = []
        for key, (timestamps, _) in self._versions.items():
            index = (
                len(timestamps) - 1
                if as_of is None
                else bisect_right(timestamps, as_of) - 1
            )
            if index >= 0 and _matches_terms(key, terms, fields, exact=exact):
                keys.append(key)
        return keys

    def close(self) -> None:
        return None
