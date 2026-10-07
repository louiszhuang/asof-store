from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons

    from ._abc import AsOfStoreABC


class AsOfView[Timestamp: SupportsAllComparisons, Key, Value]:
    """A view of a store queried at one fixed timestamp."""

    def __init__(
        self,
        store: AsOfStoreABC[Timestamp, Key, Value],
        as_of: Timestamp,
    ) -> None:
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