import asyncio
import json
from datetime import UTC, datetime
from io import StringIO
from types import NoneType
from typing import Any

import pytest

from asof_store import AsOfStore, ib_scraper
from asof_store.ib import InstrumentScrapePage, InstrumentScrapeSummary
from asof_store.ib_models import Exchange, Fund, Instrument, InstrumentSummaryItem


def _instrument(
    conid: int | None,
    symbol: str,
    **extra: Any,
) -> Instrument:
    return Instrument.model_validate(
        {"conid": conid, "symbol": symbol, "type": "STK", **extra}
    )


def _exchange(
    exchange_id: str | None,
    country_code: str | None,
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
    missing_primary_key_store = AsOfStore.from_memory()
    output = StringIO()
    progress = []
    callback_order: list[str] = []
    summary_event = InstrumentScrapeSummary(
        (InstrumentSummaryItem(productType="STK", totalCount=2),)
    )
    monkeypatch.setattr(
        ib_scraper,
        "scrape_instruments",
        lambda *args, **kwargs: iter(
            [
                summary_event,
                _instrument(1, "FIRST"),
                _instrument(None, "NO-ID"),
                _instrument(2, "UNCHANGED"),
                InstrumentScrapePage(
                    product_type="STK",
                    page_number=1,
                    page_size=500,
                    instrument_count=3,
                ),
            ]
        ),
    )

    def on_progress(report: ib_scraper.ScrapeReport) -> None:
        progress.append(report)
        callback_order.append("process")

    first = ib_scraper.scrape_and_store_instruments(
        store,
        missing_primary_key_store=missing_primary_key_store,
        print_new=True,
        output=output,
        on_progress=on_progress,
        on_summary=lambda summary: callback_order.append(summary.event),
    )

    assert first.total == 3
    assert (
        first.new_with_primary_key,
        first.changed_with_primary_key,
        first.unchanged_with_primary_key,
        first.new_without_primary_key,
        first.unchanged_without_primary_key,
    ) == (2, 0, 0, 1, 0)
    assert (
        missing_primary_key_store.put(
            first.as_of,
            _instrument(None, "NO-ID"),
            None,
        )
        is False
    )
    assert len(progress) == 1
    assert progress[0].instrument_range == (1, 3)
    assert progress[0].product_type == "STK"
    assert callback_order == ["summary", "process"]
    assert store.get(first.as_of, 1) == _instrument(1, "FIRST")
    printed = [json.loads(line) for line in output.getvalue().splitlines() if line]
    assert len(printed) == 3
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
        missing_primary_key_store=missing_primary_key_store,
        print_changes=True,
        output=output,
    )

    assert second.total == 3
    assert (
        second.new_with_primary_key,
        second.changed_with_primary_key,
        second.unchanged_with_primary_key,
        second.new_without_primary_key,
        second.unchanged_without_primary_key,
    ) == (1, 1, 1, 0, 0)
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
    missing_primary_key_store = AsOfStore.from_memory()

    async def fake_scrape(*args: Any, **kwargs: Any):
        yield InstrumentScrapeSummary(
            (InstrumentSummaryItem(productType="STK", totalCount=1),)
        )
        yield _instrument(42, "ASYNC")
        yield _instrument(None, "MISSING")
        yield InstrumentScrapePage(
            product_type="STK",
            page_number=3,
            page_size=100,
            instrument_count=2,
        )

    monkeypatch.setattr(ib_scraper, "scrape_instruments_async", fake_scrape)

    callback_order: list[str] = []
    report = asyncio.run(
        ib_scraper.async_scrape_and_store_instruments(
            store,
            missing_primary_key_store=missing_primary_key_store,
            on_summary=lambda summary: callback_order.append(summary.event),
            on_progress=lambda report: callback_order.append("process"),
        )
    )

    assert report.total == 2
    assert report.new_with_primary_key == 1
    assert report.new_without_primary_key == 1
    assert callback_order == ["summary", "process"]
    assert store.get(report.as_of, 42) == _instrument(42, "ASYNC")
    assert (
        missing_primary_key_store.put(
            report.as_of,
            _instrument(None, "MISSING"),
            None,
        )
        is False
    )


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
    missing_primary_key_store = AsOfStore.from_sql(
        "sqlite:///:memory:",
        "ib_instrument_missing_id_versions",
        datetime,
        Instrument,
        NoneType,
    )
    monkeypatch.setattr(
        ib_scraper,
        "scrape_instruments",
        lambda *args, **kwargs: iter(
            [_instrument(7, "SQL"), _instrument(None, "NO-CONID")]
        ),
    )
    try:
        first = ib_scraper.scrape_and_store_instruments(
            store,
            missing_primary_key_store=missing_primary_key_store,
        )
        second = ib_scraper.scrape_and_store_instruments(
            store,
            missing_primary_key_store=missing_primary_key_store,
        )

        assert first.total == 2
        assert first.new_with_primary_key == 1
        assert first.new_without_primary_key == 1
        assert second.total == 2
        assert second.unchanged_with_primary_key == 1
        assert second.unchanged_without_primary_key == 1
        assert store.get(second.as_of, 7) == _instrument(7, "SQL")
        assert (
            missing_primary_key_store.put(
                second.as_of,
                _instrument(None, "NO-CONID"),
                None,
            )
            is False
        )
    finally:
        store.close()
        missing_primary_key_store.close()


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
            new_with_primary_key=1,
            product_type="STK",
            instrument_range=(1001, 1500),
        )
        kwargs["on_summary"](
            InstrumentScrapeSummary(
                (InstrumentSummaryItem(productType="STK", totalCount=25),)
            )
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
            "--missing-primary-key-table-name",
            "instruments_missing",
            "--product-type",
            "STK",
            "--start-page-number",
            "3",
            "--end-page-number",
            "7",
            "--product-country",
            "US",
            "--new-product",
            "T",
            "--sort-field",
            "currency",
            "--sort-direction",
            "desc",
            "--print-new",
            "--print-changes",
            "--progress-every",
            "1",
        ]
    )

    assert result == 0
    assert created == [
        ("sqlite:///:memory:", "instruments", datetime, int, Instrument),
        (
            "sqlite:///:memory:",
            "instruments_missing",
            datetime,
            Instrument,
            NoneType,
        ),
    ]
    assert captured["product_type"] == ["STK"]
    assert captured["missing_primary_key_store"] is store
    assert captured["start_page_number"] == 3
    assert captured["end_page_number"] == 7
    assert captured["product_country"] == ["US"]
    assert captured["new_product"] == "T"
    assert captured["sort_field"] == "currency"
    assert captured["sort_direction"] == "desc"
    assert captured["print_new"] is True
    assert captured["print_changes"] is True
    assert store.closed is True
    stderr = capsys.readouterr().err
    assert "Progress instruments 1001-1500 (STK); total=1" in stderr
    assert "Scrape complete: total=1, new_with_primary_key=1" in stderr
    assert stderr.index("Instrument summary: STK=25") < stderr.index(
        "Progress instruments 1001-1500 (STK)"
    )


def test_cli_uses_default_instrument_template_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStore:
        def close(self) -> None:
            pass

    created: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        AsOfStore,
        "from_sql",
        classmethod(lambda cls, *args: created.append(args) or FakeStore()),
    )
    monkeypatch.setattr(
        ib_scraper,
        "scrape_and_store_instruments",
        lambda store, **kwargs: ib_scraper.ScrapeReport(
            as_of=datetime(2026, 1, 1, tzinfo=UTC)
        ),
    )

    ib_scraper.main(["scrape-instruments", "--sql-uri", "sqlite:///:memory:"])

    assert created[1] == (
        "sqlite:///:memory:",
        "ib_instrument_templates",
        datetime,
        Instrument,
        NoneType,
    )


def test_instrument_cli_dry_run_reports_summary_without_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    def fake_summary(**kwargs: Any) -> list[InstrumentSummaryItem]:
        captured.update(kwargs)
        return [
            InstrumentSummaryItem(productType="STK", totalCount=123),
            InstrumentSummaryItem(productType="BOND", totalCount=7),
        ]

    monkeypatch.setattr(ib_scraper, "get_instrument_summary", fake_summary)
    monkeypatch.setattr(
        AsOfStore,
        "from_sql",
        classmethod(lambda cls, *args: pytest.fail("dry-run opened a store")),
    )
    monkeypatch.setattr(
        ib_scraper,
        "scrape_and_store_instruments",
        lambda *args, **kwargs: pytest.fail("dry-run scraped product pages"),
    )

    result = ib_scraper.main(
        [
            "scrape-instruments",
            "--dry-run",
            "--product-type",
            "STK",
            "--product-country",
            "US",
            "--new-product",
            "T",
        ]
    )

    assert result == 0
    assert captured == {
        "domain": "uk",
        "product_type": ["STK"],
        "product_country": ["US"],
        "new_product": "T",
        "timeout": 30,
    }
    stderr = capsys.readouterr().err
    assert stderr.strip() == "Instrument summary: STK=123, BOND=7"


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


def test_cli_rejects_same_instrument_tables() -> None:
    with pytest.raises(ValueError, match="table-name.*must differ"):
        ib_scraper.main(
            [
                "scrape-instruments",
                "--sql-uri",
                "sqlite:///:memory:",
                "--table-name",
                "same",
                "--missing-primary-key-table-name",
                "same",
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
            "page number limits require exactly one --product-type",
        ),
        (["--start-page-number", "2"], "page number limits require exactly one"),
        (["--end-page-number", "2"], "page number limits require exactly one"),
        (
            [
                "--start-page-number",
                "3",
                "--end-page-number",
                "2",
                "--product-type",
                "STK",
            ],
            "end-page-number must be at least --start-page-number",
        ),
        (["--end-page-number", "0", "--product-type", "STK"], "--end-page-number"),
    ],
)
def test_cli_validates_page_number_limits(
    extra_args: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ib_scraper.main(
            ["scrape-instruments", "--sql-uri", "sqlite:///:memory:", *extra_args]
        )


def test_exchange_primary_key_and_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AsOfStore.from_memory()
    missing_primary_key_store = AsOfStore.from_memory()
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
        missing_primary_key_store=missing_primary_key_store,
        print_new=True,
        output=output,
    )

    assert exchange.primary_key == ("LSE", "GB")
    assert report.new_with_primary_key == 2
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
    changed = ib_scraper.scrape_and_store_exchanges(
        store,
        missing_primary_key_store=missing_primary_key_store,
        print_changes=True,
    )
    assert changed.changed_with_primary_key == 1
    value = store.get(changed.as_of, exchange.primary_key)
    assert value is not None
    assert value.name == "Updated"


def test_exchange_without_primary_key_uses_template_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AsOfStore.from_memory()
    missing_primary_key_store = AsOfStore.from_memory()
    template = _exchange("", "GB")
    monkeypatch.setattr(
        ib_scraper,
        "scrape_exchanges",
        lambda *args, **kwargs: [template],
    )

    first = ib_scraper.scrape_and_store_exchanges(
        store,
        missing_primary_key_store=missing_primary_key_store,
    )
    second = ib_scraper.scrape_and_store_exchanges(
        store,
        missing_primary_key_store=missing_primary_key_store,
    )

    assert template.primary_key is None
    assert first.new_without_primary_key == 1
    assert second.unchanged_without_primary_key == 1
    assert missing_primary_key_store.put(first.as_of, template, None) is False
    assert store.get(first.as_of, ("", "GB")) is None

    no_country_code = _exchange("LSE", None)
    assert no_country_code.primary_key is None
    no_exchange_id = _exchange(None, "GB")
    assert no_exchange_id.primary_key is None


def test_instrument_is_hashable_as_memory_store_key() -> None:
    store = AsOfStore.from_memory()
    instrument = _instrument(None, "NO-ID")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)

    assert hash(instrument) == hash(_instrument(None, "NO-ID"))
    assert store.put(timestamp, instrument, None) is True
    assert store.get(timestamp, _instrument(None, "NO-ID")) is None
    assert store.put(datetime(2026, 1, 2, tzinfo=UTC), instrument, None) is False


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
    missing_primary_key_store = AsOfStore.from_sql(
        "sqlite:///:memory:",
        "ib_exchange_template_versions",
        datetime,
        Exchange,
        NoneType,
    )
    template = _exchange("", "GB")

    async def fake_scrape(*args: Any, **kwargs: Any):
        yield exchange

    async def fake_template_scrape(*args: Any, **kwargs: Any):
        yield template

    monkeypatch.setattr(ib_scraper, "scrape_exchanges_async", fake_scrape)

    try:
        report = asyncio.run(
            ib_scraper.async_scrape_and_store_exchanges(
                store,
                missing_primary_key_store=missing_primary_key_store,
            )
        )
        assert report.new_with_primary_key == 1
        assert store.get(report.as_of, ("LSE", "GB")) == exchange
        monkeypatch.setattr(
            ib_scraper,
            "scrape_exchanges_async",
            fake_template_scrape,
        )
        missing_report = asyncio.run(
            ib_scraper.async_scrape_and_store_exchanges(
                store,
                missing_primary_key_store=missing_primary_key_store,
            )
        )
        assert missing_report.new_without_primary_key == 1
        assert (
            missing_primary_key_store.put(
                missing_report.as_of,
                template,
                None,
            )
            is False
        )
    finally:
        store.close()
        missing_primary_key_store.close()


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
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        ib_scraper,
        "scrape_and_store_exchanges",
        lambda store_arg, **kwargs: (
            captured.update(kwargs)
            or ib_scraper.ScrapeReport(
                as_of=datetime(2026, 1, 1, tzinfo=UTC),
                new_with_primary_key=1,
            )
        ),
    )

    result = ib_scraper.main(["scrape-exchanges", "--sql-uri", "sqlite:///:memory:"])

    assert result == 0
    assert created == [
        ("sqlite:///:memory:", "ib_exchanges", datetime, tuple, Exchange),
        (
            "sqlite:///:memory:",
            "ib_exchange_templates",
            datetime,
            Exchange,
            NoneType,
        ),
    ]
    assert captured["missing_primary_key_store"] is store
    assert store.closed is True
    assert "Scrape complete: total=1, new_with_primary_key=1" in capsys.readouterr().err


def test_scrape_and_store_funds_classifies_records_and_prints_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AsOfStore.from_memory()
    missing_primary_key_store = AsOfStore.from_memory()
    output = StringIO()
    funds = [
        Fund.model_validate({"CONID": "42", "SYMBOL": "FUND"}),
        Fund.model_validate({"SYMBOL": "NO-ID"}),
    ]

    def fake_scrape(*args: Any, **kwargs: Any):
        yield from funds
        kwargs["on_page"](1, len(funds))

    monkeypatch.setattr(ib_scraper, "scrape_funds", fake_scrape)
    progress: list[ib_scraper.ScrapeReport] = []

    report = ib_scraper.scrape_and_store_funds(
        store,
        missing_primary_key_store=missing_primary_key_store,
        print_new=True,
        output=output,
        on_progress=progress.append,
    )

    assert report.total == 2
    assert report.new_with_primary_key == 1
    assert report.new_without_primary_key == 1
    assert progress[0].product_type == "FUND"
    assert progress[0].instrument_range == (1, 2)
    assert store.get(report.as_of, 42) == funds[0]
    assert missing_primary_key_store.put(report.as_of, funds[1], None) is False
    printed = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [item["event"] for item in printed] == ["new", "new"]
    assert printed[0]["fund"]["CONID"] == 42


def test_fund_cli_uses_conid_and_template_stores(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def close(self) -> None:
            pass

    created: list[tuple[Any, ...]] = []
    store = FakeStore()
    monkeypatch.setattr(
        AsOfStore,
        "from_sql",
        classmethod(lambda cls, *args: created.append(args) or store),
    )
    captured: dict[str, Any] = {}

    def fake_scrape(store_arg: Any, **kwargs: Any) -> ib_scraper.ScrapeReport:
        captured.update(kwargs)
        return ib_scraper.ScrapeReport(
            as_of=datetime(2026, 1, 1, tzinfo=UTC),
            new_with_primary_key=1,
        )

    monkeypatch.setattr(ib_scraper, "scrape_and_store_funds", fake_scrape)

    result = ib_scraper.main(
        [
            "scrape-funds",
            "--sql-uri",
            "sqlite:///:memory:",
            "--product-country",
            "US",
            "--product-symbol",
            "ABC",
            "--start-page-number",
            "2",
            "--end-page-number",
            "5",
        ]
    )

    assert result == 0
    assert created == [
        ("sqlite:///:memory:", "ib_funds", datetime, int, Fund),
        (
            "sqlite:///:memory:",
            "ib_fund_templates",
            datetime,
            Fund,
            NoneType,
        ),
    ]
    assert captured["product_country"] == ["US"]
    assert captured["product_symbol"] == "ABC"
    assert captured["start_page_number"] == 2
    assert captured["end_page_number"] == 5
    assert "Scrape complete: total=1" in capsys.readouterr().err
