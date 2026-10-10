import os
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from types import NoneType
from uuid import UUID, uuid4

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import func, inspect, select

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
        store.put(datetime(2024, 1, 1, 10), "naive", "rejected")


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
    engine = store._engine
    try:
        key = ["tenant", {"id": 7}]
        expected = {"payload": ["café", 1, True, None, {"nested": ["value"]}]}
        assert store.put(1, key, {"version": 1}) is True
        assert store.put(2, key, expected) is True
        assert store.put(3, key, expected) is False

        assert store.get(1, key) == {"version": 1}
        assert store.get(2, key) == expected
        assert store.get(3, ["missing"]) is None
        with engine.connect() as connection:
            version_count = connection.scalar(
                select(func.count())
                .select_from(store._versions)
                .where(store._versions.c.key == key)
            )
        assert version_count == 2

        column_types = {
            column["name"]: str(column["type"]).upper()
            for column in inspect(engine).get_columns(table_name)
        }
        assert column_types["timestamp"] == "BIGINT"
        assert column_types["key"] == "JSONB"
        assert column_types["value"] == "JSONB"

        with engine.connect() as connection:
            stored_timestamp, stored_key, stored_value = connection.execute(
                select(
                    store._versions.c.timestamp,
                    store._versions.c.key,
                    store._versions.c.value,
                ).where(store._versions.c.timestamp == 1)
            ).one()
        assert stored_timestamp == 1
        assert stored_key == key
        assert stored_value == {"version": 1}
    finally:
        store._versions.drop(engine, checkfirst=True)
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
        store._versions.drop(store._engine, checkfirst=True)
        store.close()


def test_postgresql_get_all_returns_latest_value_for_each_key() -> None:
    table_name = f"asof_all_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, str, dict)
    try:
        assert store.put(1, "a", {"country": "CA"}) is True
        assert store.put(2, "a", {"country": "US"}) is True
        assert store.put(1, "b", {"country": "US"}) is True
        assert store.put(1, "c", {"country": None}) is True

        assert store.get_all() == [
            {"country": "US"},
            {"country": "US"},
            {"country": None},
        ]
    finally:
        store._versions.drop(store._engine, checkfirst=True)
        store.close()


def test_postgresql_tuples_round_trip_as_jsonb_arrays() -> None:
    table_name = f"asof_tuple_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, int, tuple, tuple)
    try:
        key = ("tenant", "item")
        value = ("first", "second")
        assert store.put(1, key, value) is True
        assert store.get(1, key) == value

        column_types = {
            column["name"]: str(column["type"]).upper()
            for column in inspect(store._engine).get_columns(table_name)
        }
        assert column_types["key"] == "JSONB"
        assert column_types["value"] == "JSONB"
        with store._engine.connect() as connection:
            stored_key, stored_value = connection.execute(
                select(store._versions.c.key, store._versions.c.value)
            ).one()
        assert stored_key == ["tenant", "item"]
        assert stored_value == ["first", "second"]
    finally:
        store._versions.drop(store._engine, checkfirst=True)
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

        assert {
            column["name"] for column in inspect(store._engine).get_columns(table_name)
        } == {"key", "timestamp"}
    finally:
        store._versions.drop(store._engine, checkfirst=True)
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

        column_types = {
            column["name"]: str(column["type"]).upper()
            for column in inspect(store._engine).get_columns(table_name)
        }
        assert column_types["key"] == "JSONB"
        assert column_types["value"] == "JSONB"
        with store._engine.connect() as connection:
            stored_key, stored_value = connection.execute(
                select(store._versions.c.key, store._versions.c.value)
            ).one()
        assert stored_key == {"tenant": "tenant-a"}
        assert stored_value == {"name": "item", "tags": ["one", "two"]}
    finally:
        store._versions.drop(store._engine, checkfirst=True)
        store.close()


def test_postgresql_datetime_timestamp_type() -> None:
    table_name = f"asof_datetime_{uuid4().hex}"
    store = AsOfStore.from_sql(_POSTGRES_URI, table_name, datetime, str, str)
    try:
        timestamp_column = next(
            column
            for column in inspect(store._engine).get_columns(table_name)
            if column["name"] == "timestamp"
        )
        assert (
            hasattr(timestamp_column["type"], "timezone")
            and timestamp_column["type"].timezone is True
        )
        _assert_datetime_timestamp_behavior(store)
    finally:
        store._versions.drop(store._engine, checkfirst=True)
        store.close()


@pytest.mark.parametrize(
    ("timestamp_type", "earlier", "later"),
    _SORTABLE_TIMESTAMP_CASES,
)
def test_postgresql_supports_all_sortable_timestamp_types(
    timestamp_type: type, earlier, later
) -> None:
    table_name = f"asof_ts_{timestamp_type.__name__}_{uuid4().hex}"
    store = AsOfStore.from_sql(
        _POSTGRES_URI,
        table_name,
        timestamp_type,
        str,
        str,
    )
    try:
        _assert_timestamp_type_ordering(store, earlier, later)
    finally:
        store._versions.drop(store._engine, checkfirst=True)
        store.close()
