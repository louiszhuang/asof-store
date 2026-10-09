import argparse
import json
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import NoneType
from typing import Any, TextIO

from ._abc import AsOfStoreABC
from ._store import AsOfStore
from .ib import (
    InstrumentScrapeSummary,
    scrape_exchanges,
    scrape_exchanges_async,
    scrape_instruments,
    scrape_instruments_async,
)
from .ib_models import Exchange, Instrument, NewProduct

type InstrumentStore = AsOfStoreABC[datetime, int, Instrument]
type MissingPrimaryKeyStore = AsOfStoreABC[datetime, Instrument, NoneType]
type ExchangeStore = AsOfStoreABC[datetime, tuple[str, str], Exchange]
type MissingExchangePrimaryKeyStore = AsOfStoreABC[datetime, Exchange, NoneType]
type ProgressCallback = Callable[["ScrapeReport"], None]
type SummaryCallback = Callable[[InstrumentScrapeSummary], None]


@dataclass
class ScrapeReport:
    as_of: datetime
    new_with_primary_key: int = 0
    changed_with_primary_key: int = 0
    unchanged_with_primary_key: int = 0
    new_without_primary_key: int = 0
    unchanged_without_primary_key: int = 0

    @property
    def total(self) -> int:
        return (
            self.new_with_primary_key
            + self.changed_with_primary_key
            + self.unchanged_with_primary_key
            + self.new_without_primary_key
            + self.unchanged_without_primary_key
        )


def _instrument_json(instrument: Instrument) -> dict[str, object]:
    return instrument.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
    )


def _exchange_json(exchange: Exchange) -> dict[str, object]:
    return exchange.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
    )


def _instrument_diff(
    previous: dict[str, object],
    current: dict[str, object],
) -> dict[str, dict[str, object]]:
    return {
        key: {"before": previous.get(key), "after": current.get(key)}
        for key in sorted(previous.keys() | current.keys())
        if previous.get(key) != current.get(key)
        or (key in previous) != (key in current)
    }


def _record_exchange(
    store: ExchangeStore,
    missing_primary_key_store: MissingExchangePrimaryKeyStore,
    exchange: Exchange,
    report: ScrapeReport,
    *,
    print_new: bool,
    print_changes: bool,
    output: TextIO,
) -> None:
    primary_key = exchange.primary_key
    if primary_key is None:
        if missing_primary_key_store.put(report.as_of, exchange, None):
            report.new_without_primary_key += 1
            if print_new:
                _print_json(
                    output,
                    {"event": "new", "exchange": _exchange_json(exchange)},
                )
        else:
            report.unchanged_without_primary_key += 1
        return

    current = _exchange_json(exchange)
    previous = store.get(report.as_of, primary_key)
    if previous is None:
        store.put(report.as_of, primary_key, exchange)
        report.new_with_primary_key += 1
        if print_new:
            _print_json(output, {"event": "new", "exchange": current})
        return

    changes = _instrument_diff(_exchange_json(previous), current)
    if not changes:
        report.unchanged_with_primary_key += 1
        return

    timestamp = max(datetime.now(UTC), report.as_of + timedelta(microseconds=1))
    report.as_of = timestamp
    store.put(timestamp, primary_key, exchange)
    report.changed_with_primary_key += 1
    if print_changes:
        _print_json(
            output,
            {
                "event": "changed",
                "primary_key": primary_key,
                "changes": changes,
            },
        )


def _print_json(output: TextIO, value: object) -> None:
    print(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        file=output,
    )


def _record_instrument(
    store: InstrumentStore,
    missing_primary_key_store: MissingPrimaryKeyStore,
    instrument: Instrument,
    report: ScrapeReport,
    *,
    print_new: bool,
    print_changes: bool,
    output: TextIO,
) -> None:
    primary_key = instrument.primary_key
    if primary_key is None:
        stored = missing_primary_key_store.put(report.as_of, instrument, None)
        if stored:
            report.new_without_primary_key += 1
            if print_new:
                _print_json(
                    output,
                    {"event": "new", "instrument": _instrument_json(instrument)},
                )
        else:
            report.unchanged_without_primary_key += 1
        return

    current = _instrument_json(instrument)
    previous = store.get(report.as_of, primary_key)
    if previous is None:
        store.put(report.as_of, primary_key, instrument)
        report.new_with_primary_key += 1
        if print_new:
            _print_json(output, {"event": "new", "instrument": current})
        return

    changes = _instrument_diff(_instrument_json(previous), current)
    if not changes:
        report.unchanged_with_primary_key += 1
        return

    timestamp = max(datetime.now(UTC), report.as_of + timedelta(microseconds=1))
    report.as_of = timestamp
    store.put(timestamp, primary_key, instrument)
    report.changed_with_primary_key += 1
    if print_changes:
        _print_json(
            output,
            {
                "event": "changed",
                "primary_key": primary_key,
                "changes": changes,
            },
        )


def scrape_and_store_instruments(
    store: InstrumentStore,
    *,
    missing_primary_key_store: MissingPrimaryKeyStore,
    domain: str = "uk",
    page_size: int = 500,
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    start_page_number: int = 1,
    end_page_number: int | None = None,
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
    on_summary: SummaryCallback | None = None,
    client: Any | None = None,
) -> ScrapeReport:
    """Fetch IB instruments, save changes by conid, and return run counts."""
    report = ScrapeReport(as_of=datetime.now(UTC))
    output = sys.stdout if output is None else output
    instruments = scrape_instruments(
        client,
        domain=domain,
        page_size=page_size,
        product_type=product_type,
        product_country=product_country,
        new_product=new_product,
        start_page_number=start_page_number,
        end_page_number=end_page_number,
        timeout=timeout,
    )
    for item in instruments:
        if isinstance(item, InstrumentScrapeSummary):
            if on_summary is not None:
                on_summary(item)
            continue
        _record_instrument(
            store,
            missing_primary_key_store,
            item,
            report,
            print_new=print_new,
            print_changes=print_changes,
            output=output,
        )
        if on_progress is not None:
            on_progress(replace(report))
    return report


async def async_scrape_and_store_instruments(
    store: InstrumentStore,
    *,
    missing_primary_key_store: MissingPrimaryKeyStore,
    domain: str = "uk",
    page_size: int = 500,
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    start_page_number: int = 1,
    end_page_number: int | None = None,
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
    on_summary: SummaryCallback | None = None,
    client: Any | None = None,
) -> ScrapeReport:
    """Asynchronously fetch IB instruments and save changes by conid."""
    report = ScrapeReport(as_of=datetime.now(UTC))
    output = sys.stdout if output is None else output
    instruments: AsyncIterator[Instrument | InstrumentScrapeSummary] = (
        scrape_instruments_async(
            client,
            domain=domain,
            page_size=page_size,
            product_type=product_type,
            product_country=product_country,
            new_product=new_product,
            start_page_number=start_page_number,
            end_page_number=end_page_number,
            timeout=timeout,
        )
    )
    async for item in instruments:
        if isinstance(item, InstrumentScrapeSummary):
            if on_summary is not None:
                on_summary(item)
            continue
        _record_instrument(
            store,
            missing_primary_key_store,
            item,
            report,
            print_new=print_new,
            print_changes=print_changes,
            output=output,
        )
        if on_progress is not None:
            on_progress(replace(report))
    return report


def scrape_and_store_exchanges(
    store: ExchangeStore,
    *,
    missing_primary_key_store: MissingExchangePrimaryKeyStore,
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
    client: Any | None = None,
) -> ScrapeReport:
    """Fetch IB exchanges, save changes by (id, country_code), and return counts."""
    report = ScrapeReport(as_of=datetime.now(UTC))
    output = sys.stdout if output is None else output
    for exchange in scrape_exchanges(client, timeout=timeout):
        _record_exchange(
            store,
            missing_primary_key_store,
            exchange,
            report,
            print_new=print_new,
            print_changes=print_changes,
            output=output,
        )
        if on_progress is not None:
            on_progress(replace(report))
    return report


async def async_scrape_and_store_exchanges(
    store: ExchangeStore,
    *,
    missing_primary_key_store: MissingExchangePrimaryKeyStore,
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
    client: Any | None = None,
) -> ScrapeReport:
    """Asynchronously fetch and save IB exchanges by (id, country_code)."""
    report = ScrapeReport(as_of=datetime.now(UTC))
    output = sys.stdout if output is None else output
    async for exchange in scrape_exchanges_async(client, timeout=timeout):
        _record_exchange(
            store,
            missing_primary_key_store,
            exchange,
            report,
            print_new=print_new,
            print_changes=print_changes,
            output=output,
        )
        if on_progress is not None:
            on_progress(replace(report))
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ass")
    commands = parser.add_subparsers(dest="command", required=True)
    instruments_parser = commands.add_parser(
        "scrape-instruments",
        help="scrape IB instruments into a SQL-backed as-of store",
    )
    exchanges_parser = commands.add_parser(
        "scrape-exchanges",
        help="scrape IB exchanges into a SQL-backed as-of store",
    )
    for subparser, default_table in (
        (instruments_parser, "ib_instruments"),
        (exchanges_parser, "ib_exchanges"),
    ):
        subparser.add_argument("--sql-uri", required=True)
        subparser.add_argument("--table-name", default=default_table)
        subparser.add_argument("--timeout", type=float, default=30)
        subparser.add_argument("--print-new", action="store_true")
        subparser.add_argument("--print-changes", action="store_true")
        subparser.add_argument("--progress-every", type=int, default=1000)

    instruments_parser.add_argument(
        "--missing-primary-key-table-name",
        default="ib_instrument_templates",
        help="table for instruments that do not have a conid",
    )
    exchanges_parser.add_argument(
        "--missing-primary-key-table-name",
        default="ib_exchange_templates",
        help="table for exchanges missing id or country_code",
    )
    instruments_parser.add_argument("--domain", default="uk")
    instruments_parser.add_argument("--page-size", type=int, default=500)
    instruments_parser.add_argument("--product-type", action="append")
    instruments_parser.add_argument("--product-country", action="append")
    instruments_parser.add_argument(
        "--start-page-number",
        type=int,
        help="start at this page (page limits require exactly one --product-type)",
    )
    instruments_parser.add_argument(
        "--end-page-number",
        type=int,
        help="inclusive last page to fetch (requires exactly one --product-type)",
    )
    instruments_parser.add_argument(
        "--new-product",
        choices=("all", "T", "F"),
        default="all",
    )
    return parser


def _print_progress(report: ScrapeReport, output: TextIO) -> None:
    print(
        f"Total {report.total}: "
        f"new_with_primary_key={report.new_with_primary_key}, "
        f"changed_with_primary_key={report.changed_with_primary_key}, "
        f"unchanged_with_primary_key={report.unchanged_with_primary_key}, "
        f"new_without_primary_key={report.new_without_primary_key}, "
        f"unchanged_without_primary_key={report.unchanged_without_primary_key}",
        file=output,
    )


def _print_instrument_summary(
    summary: InstrumentScrapeSummary,
    output: TextIO,
) -> None:
    product_totals = ", ".join(
        f"{item.product_type}={item.total_count}" for item in summary.products
    )
    print(f"Instrument summary: {product_totals}", file=output)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.progress_every < 1:
        raise ValueError("--progress-every must be at least 1")
    if args.command == "scrape-instruments":
        if args.table_name == args.missing_primary_key_table_name:
            raise ValueError(
                "--table-name and --missing-primary-key-table-name must differ"
            )
        if args.start_page_number is not None and args.start_page_number < 1:
            raise ValueError("--start-page-number must be at least 1")
        if args.end_page_number is not None and args.end_page_number < 1:
            raise ValueError("--end-page-number must be at least 1")
        if (
            args.start_page_number is not None
            or args.end_page_number is not None
        ) and (args.product_type is None or len(args.product_type) != 1):
            raise ValueError("page number limits require exactly one --product-type")
        if (
            args.end_page_number is not None
            and args.end_page_number < (args.start_page_number or 1)
        ):
            raise ValueError(
                "--end-page-number must be at least --start-page-number"
            )
    elif args.table_name == args.missing_primary_key_table_name:
        raise ValueError(
            "--table-name and --missing-primary-key-table-name must differ"
        )

    is_instruments = args.command == "scrape-instruments"
    key_type = int if is_instruments else tuple
    value_type = Instrument if is_instruments else Exchange
    missing_primary_key_store: MissingPrimaryKeyStore | None = None
    missing_exchange_primary_key_store: MissingExchangePrimaryKeyStore | None = None
    try:
        store = AsOfStore.from_sql(
            args.sql_uri,
            args.table_name,
            datetime,
            key_type,
            value_type,
        )
        if is_instruments:
            try:
                missing_primary_key_store = AsOfStore.from_sql(
                    args.sql_uri,
                    args.missing_primary_key_table_name,
                    datetime,
                    Instrument,
                    NoneType,
                )
            except Exception:
                store.close()
                raise
        else:
            try:
                missing_exchange_primary_key_store = AsOfStore.from_sql(
                    args.sql_uri,
                    args.missing_primary_key_table_name,
                    datetime,
                    Exchange,
                    NoneType,
                )
            except Exception:
                store.close()
                raise
    except ImportError as exc:
        raise ImportError(
            "The IB scraper CLI requires SQLAlchemy; install "
            "'asof-store[sql-sqlite]' or 'asof-store[sql-postgres]'"
        ) from exc

    try:

        def report_progress(report: ScrapeReport) -> None:
            if report.total % args.progress_every == 0:
                _print_progress(report, sys.stderr)

        if is_instruments:
            assert missing_primary_key_store is not None
            result = scrape_and_store_instruments(
                store,
                missing_primary_key_store=missing_primary_key_store,
                domain=args.domain,
                page_size=args.page_size,
                product_type=args.product_type,
                product_country=args.product_country,
                new_product=args.new_product,
                start_page_number=(
                    1 if args.start_page_number is None else args.start_page_number
                ),
                end_page_number=args.end_page_number,
                timeout=args.timeout,
                print_new=args.print_new,
                print_changes=args.print_changes,
                on_progress=report_progress,
                on_summary=lambda summary: _print_instrument_summary(
                    summary,
                    sys.stderr,
                ),
            )
        else:
            assert missing_exchange_primary_key_store is not None
            result = scrape_and_store_exchanges(
                store,
                missing_primary_key_store=missing_exchange_primary_key_store,
                timeout=args.timeout,
                print_new=args.print_new,
                print_changes=args.print_changes,
                on_progress=report_progress,
            )
        if result.total % args.progress_every:
            _print_progress(result, sys.stderr)
        print(
            "Scrape complete: "
            f"total={result.total}, "
            f"new_with_primary_key={result.new_with_primary_key}, "
            f"changed_with_primary_key={result.changed_with_primary_key}, "
            f"unchanged_with_primary_key={result.unchanged_with_primary_key}, "
            f"new_without_primary_key={result.new_without_primary_key}, "
            f"unchanged_without_primary_key={result.unchanged_without_primary_key}, "
            f"as_of={result.as_of.isoformat()}",
            file=sys.stderr,
        )
    finally:
        store.close()
        if missing_primary_key_store is not None:
            missing_primary_key_store.close()
        if missing_exchange_primary_key_store is not None:
            missing_exchange_primary_key_store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
