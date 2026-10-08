import argparse
import json
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

from . import AsOfStore
from ._abc import AsOfStoreABC
from .ib import scrape_instruments, scrape_instruments_async
from .ib_models import Instrument, NewProduct

type InstrumentStore = AsOfStoreABC[datetime, int, Instrument]
type ProgressCallback = Callable[["ScrapeReport"], None]


@dataclass
class ScrapeReport:
    as_of: datetime
    processed: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped_missing_primary_key: int = 0


def _instrument_json(instrument: Instrument) -> dict[str, object]:
    return instrument.model_dump(
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


def _print_json(output: TextIO, value: object) -> None:
    print(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        file=output,
    )


def _record_instrument(
    store: InstrumentStore,
    instrument: Instrument,
    report: ScrapeReport,
    *,
    print_new: bool,
    print_changes: bool,
    output: TextIO,
) -> None:
    report.processed += 1
    primary_key = instrument.primary_key
    if primary_key is None:
        report.skipped_missing_primary_key += 1
        return

    current = _instrument_json(instrument)
    previous = store.get(report.as_of, primary_key)
    if previous is None:
        store.put(report.as_of, primary_key, instrument)
        report.new += 1
        if print_new:
            _print_json(output, {"event": "new", "instrument": current})
        return

    changes = _instrument_diff(_instrument_json(previous), current)
    if not changes:
        report.unchanged += 1
        return

    timestamp = max(datetime.now(UTC), report.as_of + timedelta(microseconds=1))
    report.as_of = timestamp
    store.put(timestamp, primary_key, instrument)
    report.changed += 1
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
    domain: str = "uk",
    page_size: int = 500,
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
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
        timeout=timeout,
    )
    for instrument in instruments:
        _record_instrument(
            store,
            instrument,
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
    domain: str = "uk",
    page_size: int = 500,
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
    client: Any | None = None,
) -> ScrapeReport:
    """Asynchronously fetch IB instruments and save changes by conid."""
    report = ScrapeReport(as_of=datetime.now(UTC))
    output = sys.stdout if output is None else output
    instruments: AsyncIterator[Instrument] = scrape_instruments_async(
        client,
        domain=domain,
        page_size=page_size,
        product_type=product_type,
        product_country=product_country,
        new_product=new_product,
        timeout=timeout,
    )
    async for instrument in instruments:
        _record_instrument(
            store,
            instrument,
            report,
            print_new=print_new,
            print_changes=print_changes,
            output=output,
        )
        if on_progress is not None:
            on_progress(replace(report))
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asof-store")
    commands = parser.add_subparsers(dest="command", required=True)
    ib_parser = commands.add_parser(
        "scrape-ib",
        help="scrape IB instruments into a SQL-backed as-of store",
    )
    ib_parser.add_argument("--sql-uri", required=True)
    ib_parser.add_argument("--table-name", default="ib_instruments")
    ib_parser.add_argument("--domain", default="uk")
    ib_parser.add_argument("--page-size", type=int, default=500)
    ib_parser.add_argument("--product-type", action="append")
    ib_parser.add_argument("--product-country", action="append")
    ib_parser.add_argument("--new-product", choices=("all", "T", "F"), default="all")
    ib_parser.add_argument("--timeout", type=float, default=30)
    ib_parser.add_argument("--print-new", action="store_true")
    ib_parser.add_argument("--print-changes", action="store_true")
    ib_parser.add_argument("--progress-every", type=int, default=1000)
    return parser


def _print_progress(report: ScrapeReport, output: TextIO) -> None:
    print(
        f"Processed {report.processed}: new={report.new}, "
        f"changed={report.changed}, unchanged={report.unchanged}, "
        f"skipped={report.skipped_missing_primary_key}",
        file=output,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.progress_every < 1:
        raise ValueError("--progress-every must be at least 1")

    try:
        store = AsOfStore[datetime, int, Instrument].from_sql(
            args.sql_uri,
            args.table_name,
            datetime,
            int,
            Instrument,
        )
    except ImportError as exc:
        raise ImportError(
            "The IB scraper CLI requires SQLAlchemy; install "
            "'asof-store[sql-sqlite]' or 'asof-store[sql-postgres]'"
        ) from exc

    try:

        def report_progress(report: ScrapeReport) -> None:
            if report.processed % args.progress_every == 0:
                _print_progress(report, sys.stderr)

        result = scrape_and_store_instruments(
            store,
            domain=args.domain,
            page_size=args.page_size,
            product_type=args.product_type,
            product_country=args.product_country,
            new_product=args.new_product,
            timeout=args.timeout,
            print_new=args.print_new,
            print_changes=args.print_changes,
            on_progress=report_progress,
        )
        if result.processed % args.progress_every:
            _print_progress(result, sys.stderr)
        print(
            "Scrape complete: "
            f"processed={result.processed}, new={result.new}, "
            f"changed={result.changed}, unchanged={result.unchanged}, "
            f"skipped_missing_primary_key={result.skipped_missing_primary_key}, "
            f"as_of={result.as_of.isoformat()}",
            file=sys.stderr,
        )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
