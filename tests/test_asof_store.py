from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from asof_store import AsOfStore


def _from_sql(
    sql_uri: str,
    table_name: str,
    timestamp_type: type,
    key_type: type,
    value_type: type,
) -> AsOfStore:
    try:
        return AsOfStore.from_sql(
            sql_uri, table_name, timestamp_type, key_type, value_type
        )
    except ImportError as exc:
        if "SQL storage requires optional dependencies" in str(exc):
            pytest.skip(str(exc))
        raise


def test_get_returns_latest_value_at_or_before_timestamp() -> None:
    store = AsOfStore.from_memory()
    assert type(store).__name__ == "MemoryBackend"
    store.put(20, "item", "later")
    store.put(10, "item", "earlier")
    store.put(30, "item", "latest")

    assert store.get(10, "item") == "earlier"
    assert store.get(25, "item") == "later"
    assert store.get(30, "item") == "latest"


def test_put_replaces_value_at_same_timestamp() -> None:
    store = AsOfStore.from_memory()
    store.put(10, "item", "old")
    store.put(10, "item", "new")

    assert store.get(10, "item") == "new"


def test_get_returns_none_when_no_prior_value_exists() -> None:
    store = AsOfStore.from_memory()
    store.put(10, "item", "value")

    assert store.get(9, "item") is None
    assert store.get(10, "missing") is None


def test_as_of_context_uses_fixed_timestamp() -> None:
    store = AsOfStore.from_memory()
    store.put(10, "item", "original")

    with store.as_of(15) as snapshot:
        store.put(20, "item", "future")
        assert snapshot.get("item") == "original"
        assert snapshot.get("missing") is None


def test_sqlite_memory_backend_supports_as_of_queries() -> None:
    store = _from_sql("sqlite:///:memory:", "json_versions", int, str, dict)
    text_store = _from_sql("sqlite:///:memory:", "text_versions", int, str, str)
    try:
        assert type(store).__name__ == "SqlBackend"
        store.put(20, "item", {"value": "later"})
        store.put(10, "item", {"value": "earlier"})
        store.put(10, "item", {"value": "replacement"})
        store.put(40, "json", {"nested": [1, True, None]})

        assert store.get(15, "item") == {"value": "replacement"}
        assert store.get(9, "item") is None
        with store.as_of(25) as snapshot:
            assert snapshot.get("item") == {"value": "later"}
        assert store.get(40, "json") == {"nested": [1, True, None]}
        text_store.put(30, "native", "text value")
        assert text_store.get(30, "native") == "text value"
    finally:
        store.close()
        text_store.close()


def _assert_datetime_timestamp_behavior(store: AsOfStore) -> None:
    initial = datetime(2024, 1, 1, 10, tzinfo=timezone(timedelta(hours=2)))
    equivalent_instant = datetime(2024, 1, 1, 6, tzinfo=timezone(-timedelta(hours=2)))
    later = datetime(2024, 1, 1, 9, tzinfo=UTC)
    before_initial = datetime(2024, 1, 1, 7, 59, tzinfo=UTC)

    store.put(later, "event", "later")
    store.put(initial, "event", "original")
    store.put(equivalent_instant, "event", "replaced")

    assert store.get(before_initial, "event") is None
    assert store.get(initial, "event") == "replaced"
    assert store.get(datetime(2024, 1, 1, 8, 30, tzinfo=UTC), "event") == "replaced"
    assert store.get(later, "event") == "later"
    assert store.get(later, "missing") is None
    with store.as_of(datetime(2024, 1, 1, 8, 30, tzinfo=UTC)) as snapshot:
        assert snapshot.get("event") == "replaced"

    with pytest.raises(ValueError, match="timezone-aware"):
        store.put(datetime(2024, 1, 1, 10), "naive", "rejected")  # noqa: DTZ001


def test_sqlite_datetime_timestamp_type() -> None:
    store = _from_sql("sqlite:///:memory:", "datetime_versions", datetime, str, str)
    try:
        _assert_datetime_timestamp_behavior(store)
    finally:
        store.close()


def test_json_type_uses_postgresql_jsonb() -> None:
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import Column, MetaData, Table
    from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
    from sqlalchemy.schema import CreateTable

    from asof_store._sql import _sql_type

    table = Table(
        "jsonb_probe",
        MetaData(),
        Column("timestamp", _sql_type(dict)),
        Column("key", _sql_type(list)),
        Column("value", _sql_type(dict)),
    )
    ddl = str(CreateTable(table).compile(dialect=postgresql_dialect()))
    assert ddl.count("JSONB") == 3


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


def test_postgresql_jsonb_round_trip_for_all_declared_fields() -> None:
    from sqlalchemy import inspect, select

    table_name = f"asof_jsonb_{uuid4().hex}"
    sql_uri = "postgresql://louis@fre.local/louis"
    store = _from_sql(sql_uri, table_name, dict, list, dict)
    engine = store._engine  # ty: ignore[unresolved-attribute]
    try:
        key = ["tenant", {"id": 7}]
        expected = {"payload": ["café", 1, True, None, {"nested": ["value"]}]}
        store.put({"sequence": 1}, key, {"version": 1})
        store.put({"sequence": 2}, key, {"replaced": True})
        store.put({"sequence": 2}, key, expected)

        assert store.get({"sequence": 1}, key) == {"version": 1}
        assert store.get({"sequence": 2}, key) == expected
        assert store.get({"sequence": 3}, ["missing"]) is None

        column_types = {
            column["name"]: str(column["type"]).upper()
            for column in inspect(engine).get_columns(table_name)
        }
        assert column_types["timestamp"] == "JSONB"
        assert column_types["key"] == "JSONB"
        assert column_types["value"] == "JSONB"

        with engine.connect() as connection:
            stored_timestamp, stored_key, stored_value = connection.execute(
                select(
                    store._versions.c.timestamp,  # ty: ignore[unresolved-attribute]
                    store._versions.c.key,  # ty: ignore[unresolved-attribute]
                    store._versions.c.value,  # ty: ignore[unresolved-attribute]
                ).where(store._versions.c.timestamp == {"sequence": 1})  # ty: ignore[unresolved-attribute]
            ).one()
        assert stored_timestamp == {"sequence": 1}
        assert stored_key == key
        assert stored_value == {"version": 1}
    finally:
        store._versions.drop(engine, checkfirst=True)  # ty: ignore[unresolved-attribute]
        store.close()


def test_postgresql_datetime_timestamp_type() -> None:
    from sqlalchemy import inspect

    table_name = f"asof_datetime_{uuid4().hex}"
    store = _from_sql(
        "postgresql://louis@fre.local/louis",
        table_name,
        datetime,
        str,
        str,
    )
    try:
        timestamp_column = next(
            column
            for column in inspect(store._engine).get_columns(table_name)  # ty: ignore[unresolved-attribute]
            if column["name"] == "timestamp"
        )
        assert timestamp_column["type"].timezone is True
        _assert_datetime_timestamp_behavior(store)
    finally:
        store._versions.drop(store._engine, checkfirst=True)  # ty: ignore[unresolved-attribute]
        store.close()


def test_sql_store_enforces_declared_types() -> None:
    store = _from_sql("sqlite:///:memory:", "typed_versions", int, str, dict)
    try:
        with pytest.raises(TypeError, match="Expected value of type int"):
            store.put("10", "item", {})
        with pytest.raises(TypeError, match="Expected value of type dict"):
            store.put(10, "item", "not json")
    finally:
        store.close()


def test_sql_store_uses_the_requested_table_name() -> None:
    table_name = "my_asof_versions"
    store = _from_sql("sqlite:///:memory:", table_name, int, str, dict)
    try:
        assert store._versions.name == table_name  # ty: ignore[unresolved-attribute]
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
