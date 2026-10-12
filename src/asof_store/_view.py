from __future__ import annotations

from collections.abc import Mapping, Sequence
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
        as_of: Timestamp | None,
    ) -> None:
        self._store = store
        self._as_of = as_of

    def get(self, key: Key) -> Value | None:
        return self._store.get(self._as_of, key)

    def get_all_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        *,
        exact: bool = False,
    ) -> list[Value | None]:
        """Return values in this view that match JSON fields."""
        return self._store.get_all_matching(
            filters, self._as_of, exact=exact
        )

    def get_all_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        *,
        exact: bool = False,
    ) -> list[Value | None]:
        """Match all terms against fields in values at this timestamp."""
        return self._store.get_all_matching_terms(
            terms, fields, self._as_of, exact=exact
        )

    def get_keys_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        *,
        exact: bool = False,
    ) -> list[Key]:
        """Return matching JSON keys visible at this view's timestamp."""
        return self._store.get_keys_matching(
            filters, self._as_of, exact=exact
        )

    def get_keys_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        *,
        exact: bool = False,
    ) -> list[Key]:
        """Match all terms against keys visible at this timestamp."""
        return self._store.get_keys_matching_terms(
            terms, fields, self._as_of, exact=exact
        )

    def __enter__(self) -> AsOfView[Timestamp, Key, Value]:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None
