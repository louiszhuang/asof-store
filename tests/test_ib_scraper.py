import asyncio
import json
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import pytest

from asof_store import AsOfStore, ib_scraper
from asof_store.ib_models import Exchange, Instrument


def _instrument(
    conid: int | None,
    symbol: str,
    **extra: Any,
) -> Instrument:
    return Instrument.model_validate(
        {"conid": conid, "symbol": symbol, "type": "STK", **extra}
    )


def _exchange(
    exchange_id: str,
    country_code: str,
    **extra: Any,
) -> Exchange:
    return Exchange.model_validate(
        {
            "id": exchange_id,
            "country": "United Kingdom",
            "name": f"{exchange_id} Market",
            "region": "Europe",
            "assets": "Stocks",
            "country_code": country_code,
            **extra,
        }
    )


def test_scrape_and_store_classifies_records_and_prints_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AsOfStore.from_memory()
    output = StringIO()
    progress = []
    monkeypatch.setattr(
        ib_scraper,
        "scrape_instruments",
        lambda *args, **kwargs: iter(
            [
                _instrument(1, "FIRST"),
                _instrument(None, "NO-ID"),
                _instrument(2, "UNCHANGED"),
            ]
        ),
    )

    first = ib_scraper.scrape_and_store_instruments(
        store,
        print_new=True,
        output=output,
        on_progress=progress.append,
    )

    assert (first.processed, first.new, first.changed, first.unchanged) == (3, 2, 0, 0)
    assert first.skipped_missing_primary_key == 1
    assert progress[-1] == first
    assert store.get(first.as_of, 1) == _instrument(1, "FIRST")
    printed = [json.loads(line) for line in output.getvalue().splitlines() if line]
    assert len(printed) == 2
    assert printed[0]["event"] == "new"

    monkeypatch.setattr(
        ib_scraper,
        "scrape_instruments",
        lambda *args, **kwargs: iter(
            [
                _instrument(1, "FIRST-UPDATED", newField="added"),
                _instrument(2, "UNCHANGED"),
                _instrument(3, "THIRD"),
            ]
        ),
    )
    output = StringIO()
    second = ib_scraper.scrape_and_store_instruments(
        store,
        print_changes=True,
        output=output,
    )

    assert (second.processed, second.new, second.changed, second.unchanged) == (
        3,
        1,
        1,
        1,
    )
    assert store.get(second.as_of, 1) == _instrument(
        1,
        "FIRST-UPDATED",
        newField="added",
    )
    assert store.get(first.as_of, 1) == _instrument(1, "FIRST")
    change = json.loads(output.getvalue())
    assert change["event"] == "changed"
    assert change["primary_key"] == 1
    assert change["changes"] == {
        "newField": {"before": None, "after": "added"},
        "symbol": {"before": "FIRST", "after": "FIRST-UPDATED"},
    }


def test_async_scrape_and_store_saves_instrument_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AsOfStore.from_memory()

    async def fake_scrape(*args: Any, **kwargs: Any):
        yield _instrument(42, "ASYNC")
        yield _instrument(None, "MISSING")

    monkeypatch.setattr(ib_scraper, "scrape_instruments_async", fake_scrape)

    report = asyncio.run(ib_scraper.async_scrape_and_store_instruments(store))

    assert report.processed == 2
    assert report.new == 1
    assert report.skipped_missing_primary_key == 1
    assert store.get(report.as_of, 42) == _instrument(42, "ASYNC")


def test_scrape_and_store_uses_sql_store_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("sqlalchemy")
    store = AsOfStore.from_sql(
        "sqlite:///:memory:",
        "ib_instrument_versions",
        datetime,
        int,
        Instrument,
    )
    monkeypatch.setattr(
        ib_scraper,
        "scrape_instruments",
        lambda *args, **kwargs: iter([_instrument(7, "SQL")]),
    )
    try:
        first = ib_scraper.scrape_and_store_instruments(store)
        second = ib_scraper.scrape_and_store_instruments(store)

        assert first.new == 1
        assert second.unchanged == 1
        assert store.get(second.as_of, 7) == _instrument(7, "SQL")
    finally:
        store.close()


def test_cli_wires_filters_progress_and_closes_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        closed = False

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    created: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        AsOfStore,
        "from_sql",
        classmethod(lambda cls, *args: created.append(args) or store),
    )
    captured: dict[str, Any] = {}

    def fake_scrape(store_arg: Any, **kwargs: Any) -> ib_scraper.ScrapeReport:
        captured.update(kwargs)
        report = ib_scraper.ScrapeReport(
            as_of=datetime(2026, 1, 1, tzinfo=UTC),
            processed=1,
            new=1,
        )
        kwargs["on_progress"](report)
        return report

    monkeypatch.setattr(ib_scraper, "scrape_and_store_instruments", fake_scrape)

    result = ib_scraper.main(
        [
            "scrape-instruments",
            "--sql-uri",
            "sqlite:///:memory:",
            "--table-name",
            "instruments",
            "--product-type",
            "STK",
            "--start-page-number",
            "3",
            "--product-country",
            "US",
            "--new-product",
            "T",
            "--print-new",
            "--print-changes",
            "--progress-every",
            "1",
        ]
    )

    assert result == 0
    assert created == [("sqlite:///:memory:", "instruments", datetime, int, Instrument)]
    assert captured["product_type"] == ["STK"]
    assert captured["start_page_number"] == 3
    assert captured["product_country"] == ["US"]
    assert captured["new_product"] == "T"
    assert captured["print_new"] is True
    assert captured["print_changes"] is True
    assert store.closed is True
    stderr = capsys.readouterr().err
    assert "Processed 1: new=1" in stderr
    assert "Scrape complete: processed=1, new=1" in stderr


def test_cli_rejects_nonpositive_progress_interval() -> None:
    with pytest.raises(ValueError, match="--progress-every"):
        ib_scraper.main(
            [
                "scrape-instruments",
                "--sql-uri",
                "sqlite:///:memory:",
                "--progress-every",
                "0",
            ]
        )


@pytest.mark.parametrize(
    ("extra_args", "message"),
    [
        (
            ["--start-page-number", "0", "--product-type", "STK"],
            "--start-page-number",
        ),
        (
            [
                "--start-page-number",
                "2",
                "--product-type",
                "STK",
                "--product-type",
                "BOND",
            ],
            "exactly one --product-type",
        ),
        (["--start-page-number", "2"], "exactly one --product-type"),
    ],
)
def test_cli_validates_start_page_number(
    extra_args: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ib_scraper.main(
            ["scrape-instruments", "--sql-uri", "sqlite:///:memory:", *extra_args]
        )


def test_exchange_primary_key_and_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AsOfStore.from_memory()
    output = StringIO()
    exchange = _exchange("LSE", "GB")
    same_id_different_country_code = _exchange("LSE", "DE")
    monkeypatch.setattr(
        ib_scraper,
        "scrape_exchanges",
        lambda *args, **kwargs: [exchange, same_id_different_country_code],
    )

    report = ib_scraper.scrape_and_store_exchanges(
        store,
        print_new=True,
        output=output,
    )

    assert exchange.primary_key == ("LSE", "GB")
    assert report.new == 2
    assert store.get(report.as_of, exchange.primary_key) == exchange
    assert (
        store.get(report.as_of, same_id_different_country_code.primary_key)
        == same_id_different_country_code
    )
    printed_exchanges = [
        json.loads(line)["exchange"]["country_code"]
        for line in output.getvalue().splitlines()
    ]
    assert printed_exchanges == ["GB", "DE"]

    monkeypatch.setattr(
        ib_scraper,
        "scrape_exchanges",
        lambda *args, **kwargs: [_exchange("LSE", "GB", name="Updated")],
    )
    changed = ib_scraper.scrape_and_store_exchanges(store, print_changes=True)
    assert changed.changed == 1
    value = store.get(changed.as_of, exchange.primary_key)
    assert value is not None
    assert value.name == "Updated"


def test_async_exchange_scraper_stores_sql_tuple_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("sqlalchemy")
    store = AsOfStore.from_sql(
        "sqlite:///:memory:",
        "ib_exchange_versions",
        datetime,
        tuple,
        Exchange,
    )
    exchange = _exchange("LSE", "GB")

    async def fake_scrape(*args: Any, **kwargs: Any):
        yield exchange

    monkeypatch.setattr(ib_scraper, "scrape_exchanges_async", fake_scrape)

    try:
        report = asyncio.run(ib_scraper.async_scrape_and_store_exchanges(store))
        assert report.new == 1
        assert store.get(report.as_of, ("LSE", "GB")) == exchange
    finally:
        store.close()


def test_exchange_cli_uses_tuple_key_and_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        closed = False

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    created: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        AsOfStore,
        "from_sql",
        classmethod(lambda cls, *args: created.append(args) or store),
    )
    monkeypatch.setattr(
        ib_scraper,
        "scrape_and_store_exchanges",
        lambda store_arg, **kwargs: ib_scraper.ScrapeReport(
            as_of=datetime(2026, 1, 1, tzinfo=UTC),
            processed=1,
            new=1,
        ),
    )

    result = ib_scraper.main(["scrape-exchanges", "--sql-uri", "sqlite:///:memory:"])

    assert result == 0
    assert created == [
        ("sqlite:///:memory:", "ib_exchanges", datetime, tuple, Exchange)
    ]
    assert store.closed is True
    assert "Scrape complete: processed=1, new=1" in capsys.readouterr().err
