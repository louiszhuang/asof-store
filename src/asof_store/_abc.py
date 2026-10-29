from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from service_registrar import ProviderRegistry, get_registry

from ._view import AsOfView

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


def _matches_fields(
    value: object,
    filters: Mapping[str, str] | Sequence[tuple[str, str]],
    *,
    exact: bool,
) -> bool:
    filter_items = filters.items() if isinstance(filters, Mapping) else filters
    for field, query in filter_items:
        if not isinstance(field, str) or not field:
            raise ValueError("filter field names must be non-empty strings")
        if not isinstance(query, str):
            raise TypeError("filter values must be strings")
    for field, query in filter_items:
        field_value = value
        for part in field.split("."):
            if isinstance(field_value, Mapping):
                field_value = field_value.get(part)
            else:
                field_value = getattr(field_value, part, None)
            if field_value is None:
                break
        if field_value is None or isinstance(field_value, (Mapping, list, tuple)):
            return False
        actual = str(field_value).casefold()
        expected = query.casefold()
        if not (actual == expected if exact else expected in actual):
            return False
    return True


def _field_value(value: object, field: str) -> object:
    field_value = value
    for part in field.split("."):
        if isinstance(field_value, Mapping):
            field_value = field_value.get(part)
        else:
            field_value = getattr(field_value, part, None)
        if field_value is None:
            break
    return field_value


def _matches_terms(
    value: object,
    terms: Sequence[str],
    fields: Sequence[str],
    *,
    exact: bool,
) -> bool:
    for term in terms:
        if not isinstance(term, str) or not term:
            raise ValueError("search terms must be non-empty strings")
    for field in fields:
        if not isinstance(field, str) or not field:
            raise ValueError("field names must be non-empty strings")
    for term in terms:
        expected = term.casefold()
        if not any(
            (actual := _field_value(value, field)) is not None
            and not isinstance(actual, (Mapping, list, tuple))
            and (
                str(actual).casefold() == expected
                if exact
                else expected in str(actual).casefold()
            )
            for field in fields
        ):
            return False
    return True


@runtime_checkable
class AsOfStore[Timestamp: SupportsAllComparisons, Key, Value](Protocol):
    """Protocol and factory for versioned store backends."""

    @classmethod
    def from_memory(
        cls,
        *,
        registry: ProviderRegistry | None = None,
        provider: str = "asof-store.memory",
    ) -> AsOfStore[Timestamp, Key, Value]:
        return cast(
            "AsOfStore[Timestamp, Key, Value]",
            (get_registry() if registry is None else registry).create(
                AsOfStore, provider=provider
            ),
        )

    @classmethod
    def from_sql(
        cls,
        sql_uri: str | None = None,
        table_name: str | None = None,
        timestamp_type: type[Timestamp] | None = None,
        key_type: type[Key] | None = None,
        value_type: type[Value] | None = None,
        *,
        registry: ProviderRegistry | None = None,
        provider: str = "asof-store.sql",
    ) -> AsOfStore[Timestamp, Key, Value]:
        """Create a SQL-backed store using provider config and optional URI.

        The selected provider's ``service-registrar.toml`` section supplies
        defaults. Explicit arguments override its table and type settings;
        ``sql_uri`` overrides the configured URI when provided.
        """
        if table_name is None:
            raise TypeError("from_sql() requires 'table_name'")
        if timestamp_type is None or key_type is None or value_type is None:
            raise TypeError(
                "from_sql() requires 'timestamp_type', 'key_type', and 'value_type'"
            )
        selected_registry = get_registry() if registry is None else registry
        config = dict(selected_registry._config.get(provider, {}))
        if sql_uri is not None:
            config["sql_uri"] = sql_uri
        config.update(
            {
                "table_name": table_name,
                "timestamp_type": timestamp_type,
                "key_type": key_type,
                "value_type": value_type,
            }
        )
        return cast(
            "AsOfStore[Timestamp, Key, Value]",
            selected_registry.create(
                AsOfStore,
                provider=provider,
                config=config,
            ),
        )

    def put(self, as_of: Timestamp, key: Key, value: Value) -> bool:
        """Record a newer value and report whether a version was stored."""
        ...

    def get(self, as_of: Timestamp | None, key: Key) -> Value | None:
        ...

    def get_all(self, as_of: Timestamp | None = None) -> list[Value | None]:
        ...

    def get_all_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Value | None]:
        """Return latest values matching string fields in a mapping or model.

        Field names may use dotted paths for nested mappings. This default
        implementation filters values returned by ``get_all``; SQL backends
        override it to perform the filtering in the database.
        """
        if not filters:
            return self.get_all(as_of)
        matches: list[Value | None] = []
        for value in self.get_all(as_of):
            if value is not None and _matches_fields(value, filters, exact=exact):
                matches.append(value)
        return matches

    def get_all_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Value | None]:
        """Match every term against at least one field in each latest value."""
        if not terms:
            return self.get_all(as_of)
        return [
            value
            for value in self.get_all(as_of)
            if value is not None
            and _matches_terms(value, terms, fields, exact=exact)
        ]

    def get_keys_matching(
        self,
        filters: Mapping[str, str] | Sequence[tuple[str, str]],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Key]:
        """Return stored keys matching JSON fields when a backend supports it."""
        raise NotImplementedError(
            "get_keys_matching is not supported by this store backend"
        )

    def get_keys_matching_terms(
        self,
        terms: Sequence[str],
        fields: Sequence[str],
        as_of: Timestamp | None = None,
        *,
        exact: bool = False,
    ) -> list[Key]:
        """Match every term against at least one field in JSON-compatible keys."""
        raise NotImplementedError(
            "get_keys_matching_terms is not supported by this store backend"
        )

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

    def close(self) -> None:
        """Release resources held by the backend."""
        ...

    def as_of(self, as_of: Timestamp | None) -> AsOfView[Timestamp, Key, Value]:
        return AsOfView(self, as_of)


AsOfStoreABC = AsOfStore
