import pytest
from service_registrar import ProviderRegistry

from asof_store import AsOfStore
from asof_store._providers import MEMORY_PROVIDER, SQL_PROVIDER


def test_memory_backend_is_discovered_as_a_service_plugin() -> None:
    registry = ProviderRegistry()
    registry.discover()

    store = registry.create(AsOfStore, provider="asof-store.memory")
    assert isinstance(store, AsOfStore)
    assert store.put(10, "item", "value") is True
    assert store.get(10, "item") == "value"


def test_memory_constructor_uses_supplied_registry_and_provider() -> None:
    registry = ProviderRegistry()
    registry.register("custom-memory", MEMORY_PROVIDER)

    store = AsOfStore.from_memory(registry=registry, provider="custom-memory")

    assert isinstance(store, AsOfStore)


def test_legacy_service_name_aliases_protocol() -> None:
    from asof_store import AsOfStoreABC

    assert AsOfStoreABC is AsOfStore


def test_sql_backend_is_created_from_provider_config() -> None:
    registry = ProviderRegistry(config={"asof-store.sql": {}})
    registry.register("custom-sql", SQL_PROVIDER)
    registry.discover()

    try:
        store = AsOfStore.from_sql(
            "sqlite:///:memory:",
            "plugin_versions",
            int,
            str,
            dict,
            registry=registry,
            provider="custom-sql",
        )
    except ImportError as exc:
        if "requires ADBC" in str(exc):
            pytest.skip(str(exc))
        raise

    try:
        assert store.put(10, "item", {"value": "stored"}) is True
        assert store.get(10, "item") == {"value": "stored"}
    finally:
        store.close()


def test_sql_constructor_preserves_plugin_discovery_import_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenEntryPoint:
        name = "unrelated-plugin"

        def load(self) -> object:
            raise ImportError("unrelated plugin import failed")

    def broken_entry_points(*, group: str) -> list[BrokenEntryPoint]:
        return [BrokenEntryPoint()]

    monkeypatch.setattr(
        "service_registrar.registry.entry_points", broken_entry_points
    )

    with pytest.raises(ImportError, match="unrelated plugin import failed"):
        AsOfStore.from_sql(
            "sqlite:///:memory:",
            "plugin_versions",
            int,
            str,
            dict,
            registry=ProviderRegistry(),
        )
