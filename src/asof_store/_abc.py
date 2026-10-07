from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ._view import AsOfView

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


class AsOfStoreABC[Timestamp: SupportsAllComparisons, Key, Value](ABC):
    """Abstract interface implemented by versioned store backends."""

    @abstractmethod
    def put(self, as_of: Timestamp, key: Key, value: Value) -> bool:
        """Record a newer value and report whether a version was stored."""
        raise NotImplementedError

    @abstractmethod
    def get(self, as_of: Timestamp, key: Key) -> Value | None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release resources held by the backend."""
        raise NotImplementedError

    def as_of(self, as_of: Timestamp) -> AsOfView[Timestamp, Key, Value]:
        return AsOfView(self, as_of)