import json
import os
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from types import NoneType
from uuid import UUID, uuid4

import polars as pl
import pytest

pytest.importorskip("adbc_driver_postgresql")

from asof_store import AsOfStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ASOF_STORE_RUN_POSTGRES_INTEGRATION") != "1",
        reason="set ASOF_STORE_RUN_POSTGRES_INTEGRATION=1 to enable PostgreSQL tests",
    ),
]

_POSTGRES_URI = "postgresql://louis@fre.local/louis"
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


def _query(store, statement: str, parameters: tuple = ()) -> pl.DataFrame:
    return store._read(statement, parameters)


def _json_value(value):
    return json.loads(value) if isinstance(value, str) else value


def _column_types(store, table_name: str) -> dict[str, str]:
    rows = _query(
        store,
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = $1",
        (table_name,),
    )
    return {row["column_name"]: row["data_type"] for row in rows.to_dicts()}


def _drop_table(store, table_name: str) -> None:
    store._connection.rollback()
    store._execute(f'DROP TABLE IF EXISTS "{table_name}"')
    store._connection.commit()


def _assert_datetime_timestamp_behavior(store) -> None:
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


def _assert_timestamp_type_ordering(store, earlier, later) -> None:
    assert store.put(earlier, "item", "first") is True
    assert store.put(later, "item", "second") is True
    assert store.get(earlier, "item") == "first"
    assert store.get(later, "item") == "second"
    assert store.get(earlier, "missing") is None
    with pytest.raises(ValueError, match="must be greater than the latest timestamp"):
        store.put(earlier, "item", "out of order")
    assert store.put(later, "item", "second") is False


def test_postgresql_jsonb_round_trip_for_all_declared_fields() -> None:
    table_name = f"asof_jsonb_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, list, dict)
    try:
        key = ["tenant", {"id": 7}]
        expected = {"payload": ["café", 1, True, None, {"nested": ["value"]}]}
        assert store.put(1, key, {"version": 1}) is True
        assert store.put(2, key, expected) is True
        assert store.put(3, key, expected) is False
        assert store.get(1, key) == {"version": 1}
        assert store.get(2, key) == expected
        assert store.get(3, ["missing"]) is None

        assert (
            _query(store, f'SELECT count(*) AS count FROM "{table_name}"').item(
                0, "count"
            )
            == 2
        )
        assert _column_types(store, table_name) == {
            "timestamp": "bigint",
            "key": "jsonb",
            "value": "jsonb",
        }
        row = _query(
            store,
            f'SELECT timestamp, key, value FROM "{table_name}" WHERE timestamp = $1',
            (1,),
        ).row(0, named=True)
        assert row["timestamp"] == 1
        assert _json_value(row["key"]) == key
        assert _json_value(row["value"]) == {"version": 1}
    finally:
        _drop_table(store, table_name)
        store.close()


def test_postgresql_get_unique_set_uses_latest_value_per_key() -> None:
    table_name = f"asof_unique_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, str, dict)
    try:
        assert store.put(1, "a", {"country": "CA"}) is True
        assert store.put(2, "a", {"country": "US"}) is True
        assert store.put(1, "b", {"country": "FR"}) is True
        assert store.put(1, "c", {"country": None}) is True
        assert store.put(1, "d", {"region": "GB"}) is True
        assert store.get_unique_set("country") == {"FR", "US", None}
    finally:
        _drop_table(store, table_name)
        store.close()


def test_postgresql_get_all_returns_latest_value_for_each_key() -> None:
    table_name = f"asof_all_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, str, dict)
    try:
        assert store.put(1, "a", {"country": "CA"}) is True
        assert store.put(2, "a", {"country": "US"}) is True
        assert store.put(1, "b", {"country": "US"}) is True
        assert store.put(1, "c", {"country": None}) is True
        assert store.get_all(1) == [
            {"country": "CA"},
            {"country": "US"},
            {"country": None},
        ]
        assert store.get_all(0) == []
        assert store.get_all() == [
            {"country": "US"},
            {"country": "US"},
            {"country": None},
        ]
        assert store.get_unique_set("country", 1) == {"CA", "US", None}
        assert store.get_unique_set("country", 0) == set()
    finally:
        _drop_table(store, table_name)
        store.close()


def test_postgresql_tuples_round_trip_as_jsonb_arrays() -> None:
    table_name = f"asof_tuple_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, tuple, tuple)
    try:
        key = ("tenant", "item")
        value = ("first", "second")
        assert store.put(1, key, value) is True
        assert store.get(1, key) == value
        assert _column_types(store, table_name)["key"] == "jsonb"
        row = _query(store, f'SELECT key, value FROM "{table_name}"').row(0, named=True)
        assert _json_value(row["key"]) == ["tenant", "item"]
        assert _json_value(row["value"]) == ["first", "second"]
    finally:
        _drop_table(store, table_name)
        store.close()


def test_postgresql_none_type_store_omits_value_column() -> None:
    table_name = f"asof_none_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, str, NoneType)
    try:
        assert store.put(1, "item", None) is True
        assert store.put(1, "item", None) is False
        assert store.put(2, "item", None) is False
        with pytest.raises(
            ValueError, match="must be greater than the latest timestamp"
        ):
            store.put(0, "item", None)
        assert store.get(2, "item") is None
        assert store.get_all() == [None]
        assert store.get_all(0) == []
        assert set(_column_types(store, table_name)) == {"key", "timestamp"}
    finally:
        _drop_table(store, table_name)
        store.close()


def test_postgresql_pydantic_models_round_trip_as_jsonb() -> None:
    pytest.importorskip("pydantic")
    from pydantic import BaseModel

    class Key(BaseModel):
        tenant: str

    class Payload(BaseModel):
        name: str
        tags: list[str]

    table_name = f"asof_pydantic_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, Key, Payload)
    try:
        key = Key(tenant="tenant-a")
        expected = Payload(name="item", tags=["one", "two"])
        assert store.put(1, key, expected) is True
        assert store.get(1, key) == expected
        column_types = _column_types(store, table_name)
        assert column_types["key"] == "jsonb"
        assert column_types["value"] == "jsonb"
        row = _query(store, f'SELECT key, value FROM "{table_name}"').row(0, named=True)
        assert _json_value(row["key"]) == {"tenant": "tenant-a"}
        assert _json_value(row["value"]) == {"name": "item", "tags": ["one", "two"]}
    finally:
        _drop_table(store, table_name)
        store.close()


def test_postgresql_exact_json_term_search_filters_latest_values() -> None:
    table_name = f"asof_exact_terms_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, dict, dict)
    try:
        assert store.put(1, {"id": "old"}, {"symbol": "AAPL", "type": "STK"})
        assert store.put(2, {"id": "old"}, {"symbol": "MSFT", "type": "STK"})
        assert store.put(1, {"id": "match"}, {"symbol": "aapl", "type": "stk"})
        assert store.put(1, {"id": "other"}, {"symbol": "AAPL", "type": "OPT"})

        assert store.get_all_matching_terms(
            ["AAPL", "STK"], ["symbol", "type"], exact=True
        ) == [{"symbol": "aapl", "type": "stk"}]
        as_of_matches = store.get_all_matching_terms(
            ["AAPL", "STK"], ["symbol", "type"], 1, exact=True
        )
        assert sorted(
            value["symbol"] for value in as_of_matches if value is not None
        ) == ["AAPL", "aapl"]

        key_store = AsOfStore.from_sql(
            _POSTGRES_URI, f"{table_name}_keys", int, dict, type(None)
        )
        try:
            assert key_store.put(1, {"symbol": "aapl", "type": "stk"}, None)
            assert key_store.put(1, {"symbol": "AAPL", "type": "OPT"}, None)
            assert key_store.get_keys_matching_terms(
                ["AAPL", "STK"], ["symbol", "type"], exact=True
            ) == [{"symbol": "aapl", "type": "stk"}]
        finally:
            _drop_table(key_store, f"{table_name}_keys")
            key_store.close()
    finally:
        _drop_table(store, table_name)
        store.close()


def test_postgresql_datetime_timestamp_type() -> None:
    table_name = f"asof_datetime_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, datetime, str, str)
    try:
        assert (
            _column_types(store, table_name)["timestamp"] == "timestamp with time zone"
        )
        _assert_datetime_timestamp_behavior(store)
    finally:
        _drop_table(store, table_name)
        store.close()


@pytest.mark.parametrize(
    ("timestamp_type", "earlier", "later"),
    _SORTABLE_TIMESTAMP_CASES,
)
def test_postgresql_supports_all_sortable_timestamp_types(
    timestamp_type: type, earlier, later
) -> None:
    table_name = f"asof_ts_{timestamp_type.__name__}_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, timestamp_type, str, str)
    try:
        _assert_timestamp_type_ordering(store, earlier, later)
    finally:
        _drop_table(store, table_name)
        store.close()
