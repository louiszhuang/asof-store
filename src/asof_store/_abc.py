from abc import ABC, abstractmethod
from collections.abc import Mapping
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
    def get(self, as_of: Timestamp | None, key: Key) -> Value | None:
        raise NotImplementedError

    @abstractmethod
    def get_all(self, as_of: Timestamp | None = None) -> list[Value | None]:
        raise NotImplementedError

    def get_unique_set(
        self, field: str, as_of: Timestamp | None = None
    ) -> set[str | None]:
        if not isinstance(field, str) or not field:
            raise ValueError("field must be a non-empty string")

        unique_values: set[str | None] = set()
        for value in self.get_all(as_of):
            if value is None:
                unique_values.add(None)
                continue
            if isinstance(value, Mapping):
                field_value = value.get(field)
            elif hasattr(value, field):
                field_value = getattr(value, field)
            else:
                raise TypeError(
                    "get_unique_set requires mapping or Pydantic model values"
                )

            if field_value is None:
                unique_values.add(None)
            else:
                unique_values.add(field_value)
        return unique_values

    @abstractmethod
    def close(self) -> None:
        """Release resources held by the backend."""
        raise NotImplementedError

    def as_of(self, as_of: Timestamp | None) -> AsOfView[Timestamp, Key, Value]:
        return AsOfView(self, as_of)
