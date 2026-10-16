from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from types import NoneType
from typing import TYPE_CHECKING, Any
from uuid import UUID

import polars as pl
import pytest

from asof_store import AsOfStore, AsOfStoreABC, AsOfView
from asof_store._sql import SqlBackend

if TYPE_CHECKING:
    from _typeshed import SupportsAllComparisons


def _from_sql[T: SupportsAllComparisons, K, V](
    sql_uri: str,
    table_name: str,
    timestamp_type: type[T],
    key_type: type[K],
    value_type: type[V],
) -> SqlBackend[T, K, V]:
    try:
        return AsOfStore[T, K, V].from_sql(
            sql_uri, table_name, timestamp_type, key_type, value_type
        )
    except ImportError as exc:
        if "requires ADBC" in str(
            exc
        ) or "SQL storage requires optional dependencies" in str(exc):
            pytest.skip(str(exc))
        raise


def _sqlite_columns(store: SqlBackend, table_name: str) -> list[dict[str, Any]]:
    return pl.read_database(
        f'PRAGMA table_info("{table_name}")', store._connection
    ).to_dicts()


def test_get_returns_latest_value_at_or_before_timestamp() -> None:
    store = AsOfStore.from_memory()
    assert type(store).__name__ == "MemoryBackend"
    assert isinstance(store, AsOfStoreABC)
    assert store.put(10, "item", "earlier") is True
    assert store.put(20, "item", "later") is True
    assert store.put(30, "item", "latest") is True

    assert store.get(10, "item") == "earlier"
    assert store.get(25, "item") == "later"
    assert store.get(30, "item") == "latest"
    assert store.get(None, "item") == "latest"
    with store.as_of(None) as snapshot:
        assert snapshot.get("item") == "latest"


def test_put_requires_timestamp_greater_than_latest() -> None:
    store = AsOfStore.from_memory()
    assert store.put(10, "item", "old") is True
    assert store.put(10, "item", "old") is False

    with pytest.raises(ValueError, match="must be greater than the latest timestamp"):
        store.put(10, "item", "new")
    with pytest.raises(ValueError, match="must be greater than the latest timestamp"):
        store.put(9, "item", "older")

    assert store.get(10, "item") == "old"


def test_put_skips_unchanged_values_without_recording_them() -> None:
    store = AsOfStore.from_memory()
    assert store.put(10, "item", {"value": 1}) is True
    assert store.put(20, "item", {"value": 1}) is False
    assert len(store._versions["item"][0]) == 1
    assert store.put(30, "item", {"value": 2}) is True

    assert store.get(15, "item") == {"value": 1}
    assert store.get(25, "item") == {"value": 1}
    assert store.get(30, "item") == {"value": 2}


def test_get_returns_none_when_no_prior_value_exists() -> None:
    store = AsOfStore.from_memory()
    store.put(10, "item", "value")

    assert store.get(9, "item") is None
    assert store.get(10, "missing") is None


def test_memory_store_requires_hashable_keys() -> None:
    store = AsOfStore.from_memory()

    with pytest.raises(TypeError):
        store.put(10, ["item"], "value")


def test_memory_store_supports_none_type_values() -> None:
    store = AsOfStore[int, str, NoneType].from_memory()

    assert store.put(10, "item", None) is True
    assert store._versions["item"][1] is None
    assert store.put(10, "item", None) is False
    assert store.get(10, "item") is None
    assert store.put(20, "item", None) is False


def test_memory_store_get_all_and_unique_set_at_timestamp() -> None:
    store = AsOfStore.from_memory()
    store.put(1, "a", {"country": "CA"})
    store.put(2, "a", {"country": "US"})
    store.put(1, "b", {"country": "US"})
    store.put(1, "c", {"region": "GB"})

    assert store.get_all(1) == [
        {"country": "CA"},
        {"country": "US"},
        {"region": "GB"},
    ]
    assert store.get_all() == [
        {"country": "US"},
        {"country": "US"},
        {"region": "GB"},
    ]
    assert store.get_unique_set("country", 1) == {"CA", "US", None}
    assert store.get_unique_set("country") == {"US", None}


def test_memory_store_get_all_matching_uses_latest_value_and_nested_fields() -> None:
    store = AsOfStore.from_memory()
    store.put(1, "a", {"symbol": "OLD", "meta": {"country": "GB"}})
    store.put(2, "a", {"symbol": "AAPL", "meta": {"country": "US"}})
    store.put(1, "b", {"symbol": "MSFT", "meta": {"country": "US"}})

    assert store.get_all_matching({"symbol": "old"}) == []
    assert store.get_all_matching({"meta.country": "u"}) == [
        {"symbol": "AAPL", "meta": {"country": "US"}},
        {"symbol": "MSFT", "meta": {"country": "US"}},
    ]
    assert store.get_all_matching_terms(["aap", "us"], ["symbol", "meta.country"]) == [
        {"symbol": "AAPL", "meta": {"country": "US"}}
    ]
    with store.as_of(1) as snapshot:
        assert snapshot.get_all_matching({"symbol": "old"}, exact=True) == [
            {"symbol": "OLD", "meta": {"country": "GB"}}
        ]


def test_memory_store_can_match_json_compatible_keys() -> None:
    @dataclass(frozen=True)
    class InstrumentKey:
        symbol: str

    store = AsOfStore.from_memory()
    store.put(1, InstrumentKey("OLD"), None)
    store.put(2, InstrumentKey("AAPL"), None)

    assert store.get_keys_matching({"symbol": "aap"}) == [InstrumentKey("AAPL")]


def test_memory_store_expands_history_after_none_values() -> None:
    store = AsOfStore.from_memory()
    assert store.put(10, "item", None) is True
    assert store.put(20, "item", "later") is True

    assert store.get(10, "item") is None
    assert store.get(20, "item") == "later"


def test_as_of_context_uses_fixed_timestamp() -> None:
    store = AsOfStore.from_memory()
    store.put(10, "item", "original")

    with store.as_of(15) as snapshot:
        assert isinstance(snapshot, AsOfView)
        store.put(20, "item", "future")
        assert snapshot.get("item") == "original"
        assert snapshot.get("missing") is None


def test_sqlite_memory_backend_supports_as_of_queries() -> None:
    store = _from_sql("sqlite:///:memory:", "json_versions", int, str, dict)
    text_store = _from_sql("sqlite:///:memory:", "text_versions", int, str, str)
    try:
        assert type(store).__name__ == "SqlBackend"
        assert isinstance(store, AsOfStoreABC)
        assert store.put(10, "item", {"value": "earlier"}) is True
        assert store.put(20, "item", {"value": "later"}) is True
        assert store.put(30, "item", {"value": "later"}) is False
        assert store.put(20, "item", {"value": "later"}) is False
        with pytest.raises(
            ValueError, match="must be greater than the latest timestamp"
        ):
            store.put(20, "item", {"value": "duplicate timestamp"})
        with pytest.raises(
            ValueError, match="must be greater than the latest timestamp"
        ):
            store.put(15, "item", {"value": "out of order"})
        assert store.put(40, "json", {"nested": [1, True, None]}) is True

        assert store.get(15, "item") == {"value": "earlier"}
        assert store.get(9, "item") is None
        assert store.get(None, "item") == {"value": "later"}
        with store.as_of(25) as snapshot:
            assert snapshot.get("item") == {"value": "later"}
        with store.as_of(None) as snapshot:
            assert snapshot.get("item") == {"value": "later"}
        assert store.get(40, "json") == {"nested": [1, True, None]}
        assert text_store.put(30, "native", "text value") is True
        assert text_store.get(30, "native") == "text value"
        assert store.put(40, "versioned", {"symbol": "OLD"}) is True
        assert store.put(50, "versioned", {"symbol": "NEW"}) is True
        assert store.put(50, "current", {"symbol": "AAPL", "meta": {"ccy": "USD"}})
        assert store.put(60, "item", {"value": "latest", "symbol": "MSFT"})
        assert store.get_all_matching({"symbol": "old"}, as_of=45) == [
            {"symbol": "OLD"}
        ]
        assert store.get_all_matching({"symbol": "old"}, as_of=55) == []
        assert store.get_all_matching({"meta.ccy": "usd"}, as_of=55) == [
            {"symbol": "AAPL", "meta": {"ccy": "USD"}}
        ]
        assert store.get_all_matching_terms(
            ["aap", "usd"], ["symbol", "meta.ccy"], as_of=55
        ) == [{"symbol": "AAPL", "meta": {"ccy": "USD"}}]
        with store.as_of(55) as snapshot:
            assert snapshot.get_all_matching({"meta.ccy": "USD"}, exact=True) == [
                {"symbol": "AAPL", "meta": {"ccy": "USD"}}
            ]
    finally:
        store.close()
        text_store.close()


@pytest.mark.parametrize("exact", [True, False])
def test_postgres_term_search_pushes_filter_into_sql(
    monkeypatch: pytest.MonkeyPatch, exact: bool
) -> None:
    store = _from_sql("sqlite:///:memory:", "exact_terms", int, str, dict)
    statements: list[tuple[str, tuple]] = []
    try:
        store._postgres = True

        def read(statement: str, parameters=()) -> pl.DataFrame:
            statements.append((statement, tuple(parameters)))
            return pl.DataFrame({"value": [{"symbol": "aapl", "type": "stk"}]})

        monkeypatch.setattr(store, "_read", read)
        monkeypatch.setattr(
            store,
            "get_all",
            lambda *_args, **_kwargs: pytest.fail("exact search loaded all values"),
        )

        assert store.get_all_matching_terms(
            ["AAPL", "STK"], ["symbol", "type"], exact=exact
        ) == [{"symbol": "aapl", "type": "stk"}]
        statement, parameters = statements[0]
        assert "NOT EXISTS" in statement
        assert "ROW_NUMBER()" not in statement
        assert 'lower(jsonb_extract_path_text("value"' in statement
        assert " LIKE " in statement
        if exact:
            assert parameters == ("aapl", "aapl", "stk", "stk")
        else:
            assert parameters == ("%aapl%", "%aapl%", "%stk%", "%stk%")
    finally:
        store.close()


def test_postgres_trigram_indexes_are_idempotently_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _from_sql("sqlite:///:memory:", "search_indexes", int, str, dict)
    statements: list[str] = []
    try:
        store._postgres = True
        monkeypatch.setattr(
            store, "_execute", lambda statement: statements.append(statement)
        )

        names = store.create_trigram_indexes(
            ["symbol", "contract.exchange", "symbol"], column="value"
        )

        assert len(names) == 1
        assert names[0].startswith("asof_trgm_")
        assert statements[0] == "CREATE EXTENSION IF NOT EXISTS pg_trgm"
        assert "USING GIN" in statements[1]
        assert (
            "lower(jsonb_extract_path_text(\"value\", 'symbol')) gin_trgm_ops"
            in statements[1]
        )
        assert (
            "lower(jsonb_extract_path_text(\"value\", 'contract', 'exchange')) "
            "gin_trgm_ops"
        ) in statements[1]
    finally:
        store.close()


def test_sqlite_store_supports_none_type_values() -> None:
    store = _from_sql(
        "sqlite:///:memory:",
        "none_versions",
        int,
        str,
        NoneType,
    )
    key_store = _from_sql(
        "sqlite:///:memory:",
        "json_key_versions",
        int,
        dict,
        NoneType,
    )
    try:
        assert store.put(10, "item", None) is True
        assert store.put(10, "item", None) is False
        assert store.get(10, "item") is None
        assert store.put(20, "item", None) is False
        assert {
            column["name"] for column in _sqlite_columns(store, "none_versions")
        } == {"key", "timestamp"}
        with pytest.raises(
            ValueError, match="must be greater than the latest timestamp"
        ):
            store.put(9, "item", None)
        assert key_store.put(10, {"symbol": "OLD"}, None) is True
        assert key_store.put(20, {"symbol": "AAPL"}, None) is True
        assert key_store.get_keys_matching({"symbol": "old"}, as_of=15) == [
            {"symbol": "OLD"}
        ]
        assert key_store.get_keys_matching({"symbol": "old"}) == [{"symbol": "OLD"}]
        assert (
            key_store.get_keys_matching_terms(["app", "old"], ["symbol"], as_of=15)
            == []
        )
        assert key_store.get_keys_matching_terms(["aap"], ["symbol"], as_of=25) == [
            {"symbol": "AAPL"}
        ]
    finally:
        store.close()
        key_store.close()


def _assert_datetime_timestamp_behavior(store: AsOfStoreABC) -> None:
    initial = datetime(2024, 1, 1, 10, tzinfo=timezone(timedelta(hours=2)))
    equivalent_instant = datetime(2024, 1, 1, 6, tzinfo=timezone(-timedelta(hours=2)))
    later = datetime(2024, 1, 1, 9, tzinfo=UTC)
    before_initial = datetime(2024, 1, 1, 7, 59, tzinfo=UTC)

    assert store.put(initial, "event", "original") is True
    with pytest.raises(ValueError, match="must be greater than the latest timestamp"):
        store.put(equivalent_instant, "event", "replaced")
    assert store.put(later, "event", "later") is True

    assert store.get(before_initial, "event") is None
    assert store.get(initial, "event") == "original"
    assert store.get(datetime(2024, 1, 1, 8, 30, tzinfo=UTC), "event") == "original"
    assert store.get(later, "event") == "later"
    assert store.put(later + timedelta(seconds=1), "event", "later") is False
    assert store.get(later, "missing") is None
    with store.as_of(datetime(2024, 1, 1, 8, 30, tzinfo=UTC)) as snapshot:
        assert snapshot.get("event") == "original"

    with pytest.raises(ValueError, match="timezone-aware"):
        store.put(datetime(2024, 1, 1, 10), "naive", "rejected")  # noqa: DTZ001


def test_sqlite_datetime_timestamp_type() -> None:
    store = _from_sql("sqlite:///:memory:", "datetime_versions", datetime, str, str)
    try:
        _assert_datetime_timestamp_behavior(store)
    finally:
        store.close()


def test_json_type_uses_postgresql_jsonb() -> None:
    from asof_store._sql import _sql_type

    assert _sql_type(dict, postgres=True) == "JSONB"
    assert _sql_type(list, postgres=True) == "JSONB"
    assert _sql_type(tuple, postgres=True) == "JSONB"
    assert _sql_type(dict, postgres=False) == "TEXT"


def test_pydantic_models_use_json_sql_type_and_round_trip_on_sqlite() -> None:
    pytest.importorskip("pydantic")
    from pydantic import BaseModel

    class Payload(BaseModel):
        name: str
        tags: list[str]

    store = _from_sql("sqlite:///:memory:", "pydantic_values", int, str, Payload)
    try:
        expected = Payload(name="item", tags=["one", "two"])
        assert store.put(1, "payload", expected) is True
        assert store.get(1, "payload") == expected
        column_type = next(
            column
            for column in _sqlite_columns(store, "pydantic_values")
            if column["name"] == "value"
        )["type"]
        assert column_type == "TEXT"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("value_type", "value"),
    [
        (dict, {"nested": [1, True, None, {"name": "café"}]}),
        (list, [1, True, None, {"name": "café"}]),
    ],
)
def test_sqlite_json_values_round_trip(value_type: type, value: dict | list) -> None:
    store = _from_sql("sqlite:///:memory:", "json_values", int, str, value_type)
    try:
        store.put(1, "json", value)
        assert store.get(1, "json") == value
    finally:
        store.close()


def test_sqlite_tuple_values_and_keys_round_trip_as_json() -> None:
    store = _from_sql("sqlite:///:memory:", "tuple_values", int, tuple, tuple)
    try:
        key = ("tenant", "item")
        value = ("first", "second")
        assert store.put(1, key, value) is True
        assert store.get(1, key) == value

        column_types = {
            column["name"]: column["type"]
            for column in _sqlite_columns(store, "tuple_values")
        }
        assert column_types["key"] == "TEXT"
        assert column_types["value"] == "TEXT"
    finally:
        store.close()


def test_sqlite_get_all_and_unique_set_at_timestamp() -> None:
    store = _from_sql("sqlite:///:memory:", "query_versions", int, str, dict)
    try:
        store.put(1, "a", {"country": "CA"})
        store.put(2, "a", {"country": "US"})
        store.put(1, "b", {"country": "US"})
        store.put(1, "c", {"region": "GB"})

        assert store.get_all(1) == [
            {"country": "CA"},
            {"country": "US"},
            {"region": "GB"},
        ]
        assert store.get_all() == [
            {"country": "US"},
            {"country": "US"},
            {"region": "GB"},
        ]
        assert store.get_unique_set("country", 1) == {"CA", "US", None}
        assert store.get_unique_set("country") == {"US", None}
        with pytest.raises(ValueError, match="non-empty string"):
            store.get_unique_set("")
        with pytest.raises(ValueError, match="non-empty string"):
            store.get_unique_set("")
    finally:
        store.close()


def test_sql_store_enforces_declared_types() -> None:
    store = _from_sql("sqlite:///:memory:", "typed_versions", int, str, dict)
    try:
        with pytest.raises(TypeError, match="Expected value of type int"):
            store.put("10", "item", {})  # ty: ignore[invalid-argument-type]
        with pytest.raises(TypeError, match="Expected value of type dict"):
            store.put(10, "item", "not json")  # ty: ignore[invalid-argument-type]
    finally:
        store.close()


@pytest.mark.parametrize("timestamp_type", [bool, dict, list, object])
def test_sql_store_rejects_non_sortable_timestamp_types(
    timestamp_type: type,
) -> None:
    with pytest.raises(TypeError, match="indexable, sortable SQL scalar type"):
        AsOfStore.from_sql(
            "not-a-valid-uri",
            "invalid_timestamps",
            timestamp_type,
            str,
            str,
        )


_SORTABLE_TIMESTAMP_CASES = [
    (int, 1, 2),
    (float, 1.25, 2.5),
    (str, "a", "b"),
    (bytes, b"a", b"b"),
    (datetime, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)),
    (date, date(2024, 1, 1), date(2024, 1, 2)),
    (Decimal, Decimal("1.25"), Decimal("2.5")),
    (UUID, UUID(int=1), UUID(int=2)),
]


def _assert_timestamp_type_ordering(
    store: AsOfStoreABC, earlier: object, later: object
) -> None:
    assert store.put(earlier, "item", "first") is True
    assert store.put(later, "item", "second") is True
    assert store.get(earlier, "item") == "first"
    assert store.get(later, "item") == "second"
    assert store.get(earlier, "missing") is None
    with pytest.raises(ValueError, match="must be greater than the latest timestamp"):
        store.put(earlier, "item", "out of order")
    assert store.put(later, "item", "second") is False


@pytest.mark.parametrize(
    ("timestamp_type", "earlier", "later"),
    _SORTABLE_TIMESTAMP_CASES,
)
def test_sqlite_supports_all_sortable_timestamp_types(
    timestamp_type: type, earlier, later
) -> None:
    store = _from_sql(
        "sqlite:///:memory:",
        f"timestamps_{timestamp_type.__name__}",
        timestamp_type,  # ty: ignore[invalid-argument-type]
        str,
        str,
    )
    try:
        _assert_timestamp_type_ordering(store, earlier, later)
    finally:
        store.close()


def test_sql_store_uses_key_and_timestamp_as_composite_primary_key() -> None:
    store = _from_sql("sqlite:///:memory:", "primary_key_versions", int, str, str)
    try:
        columns = _sqlite_columns(store, "primary_key_versions")
        assert [
            column["name"]
            for column in sorted(columns, key=lambda item: item["pk"])
            if column["pk"]
        ] == ["key", "timestamp"]
        assert {column["name"] for column in columns} == {"key", "timestamp", "value"}
    finally:
        store.close()


def test_sql_store_uses_the_requested_table_name() -> None:
    table_name = "my_asof_versions"
    store = _from_sql("sqlite:///:memory:", table_name, int, str, dict)
    try:
        assert store._table_name == table_name
    finally:
        store.close()


def test_sql_store_round_trips_decimal_values() -> None:
    store = _from_sql("sqlite:///:memory:", "decimal_versions", Decimal, str, Decimal)
    try:
        value = Decimal("12345.6789")
        store.put(Decimal("1.25"), "amount", value)

        assert store.get(Decimal("2.0"), "amount") == value
    finally:
        store.close()
