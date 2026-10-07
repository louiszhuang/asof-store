from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from asof_store import AsOfStore, AsOfStoreABC, AsOfView


def _from_sql(
    sql_uri: str,
    table_name: str,
    timestamp_type: type,
    key_type: type,
    value_type: type,
) -> AsOfStoreABC:
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
    assert isinstance(store, AsOfStoreABC)
    assert store.put(10, "item", "earlier") is True
    assert store.put(20, "item", "later") is True
    assert store.put(30, "item", "latest") is True

    assert store.get(10, "item") == "earlier"
    assert store.get(25, "item") == "later"
    assert store.get(30, "item") == "latest"


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
        with store.as_of(25) as snapshot:
            assert snapshot.get("item") == {"value": "later"}
        assert store.get(40, "json") == {"nested": [1, True, None]}
        assert text_store.put(30, "native", "text value") is True
        assert text_store.get(30, "native") == "text value"
    finally:
        store.close()
        text_store.close()


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
    from sqlalchemy import func, inspect, select

    table_name = f"asof_jsonb_{uuid4().hex}"
    sql_uri = "postgresql://louis@fre.local/louis"
    store = _from_sql(sql_uri, table_name, int, list, dict)
    engine = store._engine  # ty: ignore[unresolved-attribute]
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
                .select_from(store._versions)  # ty: ignore[unresolved-attribute]
                .where(
                    store._versions.c.key == key  # ty: ignore[unresolved-attribute]
                )
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
                    store._versions.c.timestamp,  # ty: ignore[unresolved-attribute]
                    store._versions.c.key,  # ty: ignore[unresolved-attribute]
                    store._versions.c.value,  # ty: ignore[unresolved-attribute]
                ).where(store._versions.c.timestamp == 1)  # ty: ignore[unresolved-attribute]
            ).one()
        assert stored_timestamp == 1
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
        timestamp_type,
        str,
        str,
    )
    try:
        _assert_timestamp_type_ordering(store, earlier, later)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("timestamp_type", "earlier", "later"),
    _SORTABLE_TIMESTAMP_CASES,
)
def test_postgresql_supports_all_sortable_timestamp_types(
    timestamp_type: type, earlier, later
) -> None:
    table_name = f"asof_ts_{timestamp_type.__name__}_{uuid4().hex}"
    store = _from_sql(
        "postgresql://louis@fre.local/louis",
        table_name,
        timestamp_type,
        str,
        str,
    )
    try:
        _assert_timestamp_type_ordering(store, earlier, later)
    finally:
        store._versions.drop(store._engine, checkfirst=True)  # ty: ignore[unresolved-attribute]
        store.close()


def test_sql_store_indexes_key_and_timestamp_together() -> None:
    from sqlalchemy import inspect

    store = _from_sql("sqlite:///:memory:", "indexed_versions", int, str, str)
    try:
        indexes = inspect(store._engine).get_indexes("indexed_versions")  # ty: ignore[unresolved-attribute]
        assert any(index["column_names"] == ["key", "timestamp"] for index in indexes)
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
