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
    InstrumentScrapePage,
    InstrumentScrapeSummary,
    get_instrument_summary,
    get_products_by_filters,
    scrape_exchanges,
    scrape_exchanges_async,
    scrape_funds,
    scrape_funds_async,
    scrape_instruments,
    scrape_instruments_async,
)
from .ib_models import (
    Exchange,
    Fund,
    Instrument,
    InstrumentSortField,
    NewProduct,
    SortDirection,
)

type InstrumentStore = AsOfStoreABC[datetime, int, Instrument]
type MissingPrimaryKeyStore = AsOfStoreABC[datetime, Instrument, NoneType]
type ExchangeStore = AsOfStoreABC[datetime, tuple[str, str], Exchange]
type MissingExchangePrimaryKeyStore = AsOfStoreABC[datetime, Exchange, NoneType]
type FundStore = AsOfStoreABC[datetime, int, Fund]
type MissingFundPrimaryKeyStore = AsOfStoreABC[datetime, Fund, NoneType]
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
    product_type: str | None = None
    instrument_range: tuple[int, int] | None = None

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


def _fund_json(fund: Fund) -> dict[str, object]:
    return fund.model_dump(
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


def _record_fund(
    store: FundStore,
    missing_primary_key_store: MissingFundPrimaryKeyStore,
    fund: Fund,
    report: ScrapeReport,
    *,
    print_new: bool,
    print_changes: bool,
    output: TextIO,
) -> None:
    primary_key = fund.primary_key
    if primary_key is None:
        if missing_primary_key_store.put(report.as_of, fund, None):
            report.new_without_primary_key += 1
            if print_new:
                _print_json(output, {"event": "new", "fund": _fund_json(fund)})
        else:
            report.unchanged_without_primary_key += 1
        return

    current = _fund_json(fund)
    previous = store.get(report.as_of, primary_key)
    if previous is None:
        store.put(report.as_of, primary_key, fund)
        report.new_with_primary_key += 1
        if print_new:
            _print_json(output, {"event": "new", "fund": current})
        return

    changes = _instrument_diff(_fund_json(previous), current)
    if not changes:
        report.unchanged_with_primary_key += 1
        return

    timestamp = max(datetime.now(UTC), report.as_of + timedelta(microseconds=1))
    report.as_of = timestamp
    store.put(timestamp, primary_key, fund)
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
    sort_field: InstrumentSortField = "symbol",
    sort_direction: SortDirection = "asc",
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
        sort_field=sort_field,
        sort_direction=sort_direction,
        start_page_number=start_page_number,
        end_page_number=end_page_number,
        timeout=timeout,
    )
    for item in instruments:
        if isinstance(item, InstrumentScrapeSummary):
            if on_summary is not None:
                on_summary(item)
            continue
        if isinstance(item, InstrumentScrapePage):
            if on_progress is not None:
                first = (item.page_number - 1) * item.page_size + 1
                page_report = replace(
                    report,
                    product_type=item.product_type,
                    instrument_range=(
                        first,
                        first + item.instrument_count - 1,
                    ),
                )
                on_progress(page_report)
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
    sort_field: InstrumentSortField = "symbol",
    sort_direction: SortDirection = "asc",
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
    instruments: AsyncIterator[
        Instrument | InstrumentScrapeSummary | InstrumentScrapePage
    ] = (
        scrape_instruments_async(
            client,
            domain=domain,
            page_size=page_size,
            product_type=product_type,
            product_country=product_country,
            new_product=new_product,
            sort_field=sort_field,
            sort_direction=sort_direction,
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
        if isinstance(item, InstrumentScrapePage):
            if on_progress is not None:
                first = (item.page_number - 1) * item.page_size + 1
                page_report = replace(
                    report,
                    product_type=item.product_type,
                    instrument_range=(
                        first,
                        first + item.instrument_count - 1,
                    ),
                )
                on_progress(page_report)
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
    return report


def scrape_and_reconcile_instruments(
    store: InstrumentStore,
    *,
    missing_primary_key_store: MissingPrimaryKeyStore,
    domain: str = "uk",
    page_size: int = 500,
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    sort_field: InstrumentSortField = "symbol",
    sort_direction: SortDirection = "asc",
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
    on_summary: SummaryCallback | None = None,
    client: Any | None = None,
) -> ScrapeReport:
    """Fetch only pages needed to reconcile local instruments with IB."""
    if page_size not in (100, 200, 300, 400, 500):
        raise ValueError("page_size must be 100, 200, 300, 400, or 500")
    if client is None:
        from zapros import Client

        with Client() as active_client:
            return scrape_and_reconcile_instruments(
                store,
                missing_primary_key_store=missing_primary_key_store,
                domain=domain,
                page_size=page_size,
                product_type=product_type,
                product_country=product_country,
                new_product=new_product,
                sort_field=sort_field,
                sort_direction=sort_direction,
                timeout=timeout,
                print_new=print_new,
                print_changes=print_changes,
                output=output,
                on_progress=on_progress,
                on_summary=on_summary,
                client=active_client,
            )
    report = ScrapeReport(as_of=datetime.now(UTC))
    output = sys.stdout if output is None else output
    summaries = get_instrument_summary(
        client,
        domain=domain,
        product_type=product_type,
        product_country=product_country,
        new_product=new_product,
        timeout=timeout,
    )
    if on_summary is not None:
        on_summary(InstrumentScrapeSummary(tuple(summaries)))

    def matches_query(instrument: Instrument, kind: str) -> bool:
        if instrument.product_type != kind:
            return False
        if product_country and instrument.country not in product_country:
            return False
        return (
            new_product == "all"
            or (
                new_product == "T"
                and instrument.is_new_product in ("T", True)
            )
            or (
                new_product == "F"
                and instrument.is_new_product in ("F", False)
            )
        )

    def sort_value(instrument: Instrument) -> str | None:
        return getattr(instrument, sort_field)

    def identity(instrument: Instrument) -> tuple[str, int | str]:
        if instrument.conid is not None:
            return ("conid", instrument.conid)
        return ("model", instrument.model_dump_json(by_alias=True))

    def before(left: str, right: str) -> bool:
        return left < right if sort_direction == "asc" else left > right

    def local_items(kind: str) -> list[Instrument]:
        unique: dict[tuple[str, int | str], Instrument] = {}
        for instrument in (
            *store.get_all(None),
            *missing_primary_key_store.get_all(None),
        ):
            if isinstance(instrument, Instrument) and matches_query(instrument, kind):
                unique.setdefault(identity(instrument), instrument)
        return list(unique.values())

    def diagnose(message: str) -> None:
        print(f"Reconcile {message}", file=sys.stderr)

    for summary in summaries:
        kind = summary.product_type
        total_count = summary.total_count
        if total_count == 0:
            diagnose(f"complete product_type={kind} reason=remote_total_is_zero")
            continue
        page_count = (total_count + page_size - 1) // page_size
        known = local_items(kind)
        known_without_sort_key = sum(
            sort_value(instrument) is None for instrument in known
        )
        known_indexes = {
            identity(instrument): index for index, instrument in enumerate(known)
        }
        diagnose(
            f"start product_type={kind} total={total_count} page_size={page_size} "
            f"pages={page_count} local_matches={len(known)} "
            f"local_without_{sort_field}={known_without_sort_key} "
            f"sort={sort_field}:{sort_direction} "
            f"countries={product_country or 'all'} new_product={new_product}"
        )
        if len(known) == total_count:
            diagnose(
                f"complete product_type={kind} reason=local_count_matches_total "
                f"local={len(known)} remote={total_count}"
            )
            continue
        pages: dict[int, list[Instrument]] = {}

        def fetch_page(
            page_number: int,
            *,
            kind: str = kind,
            total_count: int = total_count,
            page_count: int = page_count,
            pages: dict[int, list[Instrument]] = pages,
            known: list[Instrument] = known,
            known_indexes: dict[tuple[str, int | str], int] = known_indexes,
        ) -> list[Instrument]:
            page = pages.get(page_number)
            if page is not None:
                diagnose(f"page cache_hit product_type={kind} page={page_number}")
                return page
            diagnose(
                f"page request product_type={kind} page={page_number}/{page_count}"
            )
            response = get_products_by_filters(
                kind,
                client,
                page_number=page_number,
                page_size=page_size,
                domain=domain,
                product_country=product_country,
                new_product=new_product,
                sort_field=sort_field,
                sort_direction=sort_direction,
                timeout=timeout,
            )
            expected_count = min(
                page_size,
                total_count - (page_number - 1) * page_size,
            )
            if len(response.products) != expected_count:
                raise ValueError(
                    f"Unexpected product count for {kind} page {page_number}: "
                    f"expected {expected_count}, received {len(response.products)}"
                )
            page = response.products
            pages[page_number] = page
            matching_count = sum(matches_query(item, kind) for item in page)
            first_key = sort_value(page[0]) if page else None
            last_key = sort_value(page[-1]) if page else None
            local_count_before = len(known)
            for instrument in page:
                _record_instrument(
                    store,
                    missing_primary_key_store,
                    instrument,
                    report,
                    print_new=print_new,
                    print_changes=print_changes,
                    output=output,
                )
                if matches_query(instrument, kind):
                    instrument_identity = identity(instrument)
                    existing_index = known_indexes.get(instrument_identity)
                    if existing_index is None:
                        known_indexes[instrument_identity] = len(known)
                        known.append(instrument)
                    else:
                        known[existing_index] = instrument
            diagnose(
                f"page received product_type={kind} page={page_number} "
                f"count={len(page)} {sort_field}_first={first_key!r} "
                f"{sort_field}_last={last_key!r} matching={matching_count} "
                f"local_matches={local_count_before}->{len(known)}"
            )
            if on_progress is not None:
                first = (page_number - 1) * page_size + 1
                on_progress(
                    replace(
                        report,
                        product_type=kind,
                        instrument_range=(
                            first,
                            first + expected_count - 1,
                        ),
                    )
                )
            return page

        def local_count_between(
            left: str,
            right: str,
            *,
            known: list[Instrument] = known,
        ) -> int:
            return sum(
                1
                for instrument in known
                if (value := sort_value(instrument)) is not None
                and before(left, value)
                and before(value, right)
            )

        def duplicate_run_is_complete(
            key: str,
            *,
            lower_anchor_page: int,
            upper_anchor_page: int,
            kind: str = kind,
            page_count: int = page_count,
            known: list[Instrument] = known,
        ) -> bool:
            def page_first(page_number: int) -> str | None:
                values = [sort_value(item) for item in fetch_page(page_number)]
                return values[0]

            def page_last(page_number: int) -> str | None:
                values = [sort_value(item) for item in fetch_page(page_number)]
                return values[-1]

            diagnose(
                f"duplicate boundary search start product_type={kind} "
                f"sort_key={key!r} pages=1-{page_count}"
            )
            low, high = 1, page_count
            while low < high:
                middle = (low + high) // 2
                last = page_last(middle)
                if last is None:
                    diagnose(
                        f"duplicate boundary search failed product_type={kind} "
                        f"phase=lower page={middle} reason=missing_sort_key"
                    )
                    return False
                old_low, old_high = low, high
                if before(last, key):
                    low = middle + 1
                else:
                    high = middle
                diagnose(
                    f"duplicate boundary search product_type={kind} "
                    f"phase=lower pages={old_low}-{old_high} probe={middle} "
                    f"page_last={last!r} next={low}-{high}"
                )
                if abs(middle - lower_anchor_page) > 1:
                    diagnose(
                        f"warning product_type={kind} phase=lower "
                        f"probe_page={middle} anchor_page={lower_anchor_page} "
                        "reason=probe_outside_neighbor_page"
                    )
            first_page = low
            first_page_last = page_last(first_page)
            if first_page_last != key:
                diagnose(
                    f"duplicate boundary search not_found product_type={kind} "
                    f"phase=lower candidate_page={first_page} "
                    f"page_last={first_page_last!r} sort_key={key!r}"
                )
                return False

            low, high = first_page, page_count
            while low < high:
                middle = (low + high + 1) // 2
                first = page_first(middle)
                if first is None:
                    diagnose(
                        f"duplicate boundary search failed product_type={kind} "
                        f"phase=upper page={middle} reason=missing_sort_key"
                    )
                    return False
                old_low, old_high = low, high
                if before(key, first):
                    high = middle - 1
                else:
                    low = middle
                diagnose(
                    f"duplicate boundary search product_type={kind} "
                    f"phase=upper pages={old_low}-{old_high} probe={middle} "
                    f"page_first={first!r} next={low}-{high}"
                )
                if abs(middle - upper_anchor_page) > 1:
                    diagnose(
                        f"warning product_type={kind} phase=upper "
                        f"probe_page={middle} anchor_page={upper_anchor_page} "
                        "reason=probe_outside_neighbor_page"
                    )
            last_page = low
            last_page_first = page_first(last_page)
            if last_page_first != key:
                diagnose(
                    f"duplicate boundary search not_found product_type={kind} "
                    f"phase=upper candidate_page={last_page} "
                    f"page_first={last_page_first!r} sort_key={key!r}"
                )
                return False

            if first_page == last_page:
                remote_count = sum(
                    sort_value(instrument) == key
                    for instrument in fetch_page(first_page)
                )
            else:
                first_values = [sort_value(item) for item in fetch_page(first_page)]
                last_values = [sort_value(item) for item in fetch_page(last_page)]
                remote_count = (
                    sum(value == key for value in first_values)
                    + sum(value == key for value in last_values)
                    + max(0, last_page - first_page - 1) * page_size
                )
            local_count = sum(sort_value(item) == key for item in known)
            first_page_matches = (
                remote_count
                if first_page == last_page
                else sum(value == key for value in first_values)
            )
            last_page_matches = (
                0
                if first_page == last_page
                else sum(value == key for value in last_values)
            )
            diagnose(
                f"duplicate region count product_type={kind} sort_key={key!r} "
                f"pages={first_page}-{last_page} first_page_matches="
                f"{first_page_matches} "
                f"middle_pages={max(0, last_page - first_page - 1)} "
                f"last_page_matches={last_page_matches} "
                f"page_size={page_size} local_key_matches={local_count} "
                f"local_total_matches={len(known)}"
            )
            if local_count == remote_count:
                diagnose(
                    f"complete product_type={kind} pages={first_page}-{last_page} "
                    f"sort_key={key!r} local={local_count} remote={remote_count}"
                )
                return True
            diagnose(
                f"duplicate region incomplete product_type={kind} "
                f"pages={first_page}-{last_page} sort_key={key!r} "
                f"local={local_count} remote={remote_count}; fetching full run"
            )
            for page_number in range(first_page, last_page + 1):
                fetch_page(page_number)
            return True

        def reconcile_interval(
            left_page: int,
            right_page: int,
            *,
            kind: str = kind,
        ) -> None:
            if right_page - left_page <= 1:
                diagnose(
                    f"complete product_type={kind} pages={left_page}-{right_page} "
                    "reason=no_unprobed_pages"
                )
                return
            left_items = fetch_page(left_page)
            right_items = fetch_page(right_page)
            left_key = sort_value(left_items[-1])
            right_key = sort_value(right_items[0])
            if left_key is None or right_key is None:
                middle = (left_page + right_page) // 2
                diagnose(
                    f"split product_type={kind} pages={left_page}-{right_page} "
                    f"reason=missing_sort_key midpoint={middle}"
                )
                fetch_page(middle)
                reconcile_interval(left_page, middle)
                reconcile_interval(middle, right_page)
                return
            if left_key == right_key:
                if duplicate_run_is_complete(
                    left_key,
                    lower_anchor_page=left_page,
                    upper_anchor_page=right_page,
                ):
                    return
            else:
                local_count = local_count_between(left_key, right_key)
                expected_count = (right_page - left_page - 1) * page_size
                if local_count == expected_count:
                    diagnose(
                        f"complete product_type={kind} pages={left_page}-{right_page} "
                        f"sort_keys={left_key!r}..{right_key!r} "
                        f"local={local_count} remote={expected_count}"
                    )
                    return

            middle = (left_page + right_page) // 2
            diagnose(
                f"split product_type={kind} pages={left_page}-{right_page} "
                f"sort_keys={left_key!r}..{right_key!r} midpoint={middle}"
            )
            fetch_page(middle)
            reconcile_interval(left_page, middle)
            reconcile_interval(middle, right_page)

        fetch_page(1)
        if page_count > 1:
            fetch_page(page_count)
            reconcile_interval(1, page_count)
    return report


def scrape_and_store_funds(
    store: FundStore,
    *,
    missing_primary_key_store: MissingFundPrimaryKeyStore,
    domain: str = "uk",
    page_size: int = 100,
    product_country: list[str] | None = None,
    product_symbol: str = "",
    new_product: NewProduct = "all",
    start_page_number: int = 1,
    end_page_number: int | None = None,
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
    client: Any | None = None,
) -> ScrapeReport:
    """Fetch IB funds, save changes by conid, and return run counts."""
    report = ScrapeReport(as_of=datetime.now(UTC))
    output = sys.stdout if output is None else output

    def report_page(page_number: int, fund_count: int) -> None:
        if on_progress is None:
            return
        first = (page_number - 1) * page_size + 1
        on_progress(
            replace(
                report,
                product_type="FUND",
                instrument_range=(first, first + fund_count - 1),
            )
        )

    for fund in scrape_funds(
        client,
        domain=domain,
        page_size=page_size,
        product_country=product_country,
        product_symbol=product_symbol,
        new_product=new_product,
        start_page_number=start_page_number,
        end_page_number=end_page_number,
        on_page=report_page,
        timeout=timeout,
    ):
        _record_fund(
            store,
            missing_primary_key_store,
            fund,
            report,
            print_new=print_new,
            print_changes=print_changes,
            output=output,
        )
    return report


async def async_scrape_and_store_funds(
    store: FundStore,
    *,
    missing_primary_key_store: MissingFundPrimaryKeyStore,
    domain: str = "uk",
    page_size: int = 100,
    product_country: list[str] | None = None,
    product_symbol: str = "",
    new_product: NewProduct = "all",
    start_page_number: int = 1,
    end_page_number: int | None = None,
    timeout: float = 30,
    print_new: bool = False,
    print_changes: bool = False,
    output: TextIO | None = None,
    on_progress: ProgressCallback | None = None,
    client: Any | None = None,
) -> ScrapeReport:
    """Asynchronously fetch and save IB funds by conid."""
    report = ScrapeReport(as_of=datetime.now(UTC))
    output = sys.stdout if output is None else output

    def report_page(page_number: int, fund_count: int) -> None:
        if on_progress is None:
            return
        first = (page_number - 1) * page_size + 1
        on_progress(
            replace(
                report,
                product_type="FUND",
                instrument_range=(first, first + fund_count - 1),
            )
        )

    async for fund in scrape_funds_async(
        client,
        domain=domain,
        page_size=page_size,
        product_country=product_country,
        product_symbol=product_symbol,
        new_product=new_product,
        start_page_number=start_page_number,
        end_page_number=end_page_number,
        on_page=report_page,
        timeout=timeout,
    ):
        _record_fund(
            store,
            missing_primary_key_store,
            fund,
            report,
            print_new=print_new,
            print_changes=print_changes,
            output=output,
        )
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
    reconcile_parser = commands.add_parser(
        "scrape-reconcile",
        help="reconcile missing IB instruments into a SQL-backed as-of store",
    )
    exchanges_parser = commands.add_parser(
        "scrape-exchanges",
        help="scrape IB exchanges into a SQL-backed as-of store",
    )
    funds_parser = commands.add_parser(
        "scrape-funds",
        help="scrape IB funds into a SQL-backed as-of store",
    )
    for subparser, default_table in (
        (instruments_parser, "ib_instruments"),
        (exchanges_parser, "ib_exchanges"),
        (funds_parser, "ib_funds"),
    ):
        subparser.add_argument("--sql-uri")
        subparser.add_argument("--table-name", default=default_table)
        subparser.add_argument("--timeout", type=float, default=30)
        subparser.add_argument("--print-new", action="store_true")
        subparser.add_argument("--print-changes", action="store_true")
        subparser.add_argument("--progress-every", type=int, default=1)

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
    funds_parser.add_argument(
        "--missing-primary-key-table-name",
        default="ib_fund_templates",
        help="table for funds that do not have a conid",
    )
    instruments_parser.add_argument("--domain", default="uk")
    instruments_parser.add_argument("--page-size", type=int, default=500)
    instruments_parser.add_argument("--product-type", action="append")
    instruments_parser.add_argument("--product-country", action="append")
    instruments_parser.add_argument(
        "--sort-field",
        choices=("currency", "country", "symbol", "exchange_id"),
        default="symbol",
    )
    instruments_parser.add_argument(
        "--sort-direction",
        choices=("asc", "desc"),
        default="asc",
    )
    instruments_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the filtered instrument summary without scraping or storing",
    )
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
    reconcile_parser.add_argument("--sql-uri")
    reconcile_parser.add_argument("--table-name", default="ib_instruments")
    reconcile_parser.add_argument(
        "--missing-primary-key-table-name",
        default="ib_instrument_templates",
        help="table for instruments that do not have a conid",
    )
    reconcile_parser.add_argument("--timeout", type=float, default=30)
    reconcile_parser.add_argument("--print-new", action="store_true")
    reconcile_parser.add_argument("--print-changes", action="store_true")
    reconcile_parser.add_argument("--progress-every", type=int, default=1)
    reconcile_parser.add_argument("--domain", default="uk")
    reconcile_parser.add_argument("--page-size", type=int, default=500)
    reconcile_parser.add_argument("--product-type", action="append")
    reconcile_parser.add_argument("--product-country", action="append")
    reconcile_parser.add_argument(
        "--sort-field",
        choices=("currency", "country", "symbol", "exchange_id"),
        default="symbol",
    )
    reconcile_parser.add_argument(
        "--sort-direction",
        choices=("asc", "desc"),
        default="asc",
    )
    reconcile_parser.add_argument(
        "--new-product",
        choices=("all", "T", "F"),
        default="all",
    )
    funds_parser.add_argument("--domain", default="uk")
    funds_parser.add_argument("--page-size", type=int, default=100)
    funds_parser.add_argument("--product-country", action="append")
    funds_parser.add_argument("--product-symbol", default="")
    funds_parser.add_argument(
        "--new-product",
        choices=("all", "T", "F"),
        default="all",
    )
    funds_parser.add_argument("--start-page-number", type=int, default=1)
    funds_parser.add_argument("--end-page-number", type=int)
    return parser


def _print_progress(report: ScrapeReport, output: TextIO) -> None:
    item_label = "funds" if report.product_type == "FUND" else "instruments"
    instrument_range = (
        f"{item_label} "
        f"{report.instrument_range[0]}-{report.instrument_range[1]} "
        f"({report.product_type})"
        if report.instrument_range is not None
        else f"total {report.total}"
    )
    print(
        f"Progress {instrument_range}; total={report.total}: "
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
    if args.command == "scrape-instruments" and args.dry_run:
        summary = get_instrument_summary(
            domain=args.domain,
            product_type=args.product_type,
            product_country=args.product_country,
            new_product=args.new_product,
            timeout=args.timeout,
        )
        _print_instrument_summary(InstrumentScrapeSummary(tuple(summary)), sys.stderr)
        return 0
    if args.sql_uri is None:
        raise ValueError("--sql-uri is required unless --dry-run is used")

    if args.command in ("scrape-instruments", "scrape-reconcile"):
        if args.table_name == args.missing_primary_key_table_name:
            raise ValueError(
                "--table-name and --missing-primary-key-table-name must differ"
            )
        if args.command == "scrape-instruments":
            if args.start_page_number is not None and args.start_page_number < 1:
                raise ValueError("--start-page-number must be at least 1")
            if args.end_page_number is not None and args.end_page_number < 1:
                raise ValueError("--end-page-number must be at least 1")
            if (
                args.start_page_number is not None
                or args.end_page_number is not None
            ) and (args.product_type is None or len(args.product_type) != 1):
                raise ValueError(
                    "page number limits require exactly one --product-type"
                )
            if (
                args.end_page_number is not None
                and args.end_page_number < (args.start_page_number or 1)
            ):
                raise ValueError(
                    "--end-page-number must be at least --start-page-number"
                )
    elif args.command == "scrape-funds":
        if args.table_name == args.missing_primary_key_table_name:
            raise ValueError(
                "--table-name and --missing-primary-key-table-name must differ"
            )
        if args.start_page_number < 1:
            raise ValueError("--start-page-number must be at least 1")
        if args.end_page_number is not None and (
            args.end_page_number < args.start_page_number
        ):
            raise ValueError(
                "--end-page-number must be at least --start-page-number"
            )
    elif args.table_name == args.missing_primary_key_table_name:
        raise ValueError(
            "--table-name and --missing-primary-key-table-name must differ"
        )

    is_instruments = args.command in ("scrape-instruments", "scrape-reconcile")
    is_funds = args.command == "scrape-funds"
    key_type = int if is_instruments or is_funds else tuple
    value_type = Instrument if is_instruments else Fund if is_funds else Exchange
    missing_primary_key_store: MissingPrimaryKeyStore | None = None
    missing_exchange_primary_key_store: MissingExchangePrimaryKeyStore | None = None
    missing_fund_primary_key_store: MissingFundPrimaryKeyStore | None = None
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
        elif is_funds:
            try:
                missing_fund_primary_key_store = AsOfStore.from_sql(
                    args.sql_uri,
                    args.missing_primary_key_table_name,
                    datetime,
                    Fund,
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

        progress_events = 0

        def report_progress(report: ScrapeReport) -> None:
            nonlocal progress_events
            progress_events += 1
            if progress_events % args.progress_every == 0:
                _print_progress(report, sys.stderr)

        if is_instruments:
            assert missing_primary_key_store is not None
            if args.command == "scrape-reconcile":
                result = scrape_and_reconcile_instruments(
                    store,
                    missing_primary_key_store=missing_primary_key_store,
                    domain=args.domain,
                    page_size=args.page_size,
                    product_type=args.product_type,
                    product_country=args.product_country,
                    new_product=args.new_product,
                    sort_field=args.sort_field,
                    sort_direction=args.sort_direction,
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
                result = scrape_and_store_instruments(
                    store,
                    missing_primary_key_store=missing_primary_key_store,
                    domain=args.domain,
                    page_size=args.page_size,
                    product_type=args.product_type,
                    product_country=args.product_country,
                    new_product=args.new_product,
                    sort_field=args.sort_field,
                    sort_direction=args.sort_direction,
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
        elif is_funds:
            assert missing_fund_primary_key_store is not None
            result = scrape_and_store_funds(
                store,
                missing_primary_key_store=missing_fund_primary_key_store,
                domain=args.domain,
                page_size=args.page_size,
                product_country=args.product_country,
                product_symbol=args.product_symbol,
                new_product=args.new_product,
                start_page_number=args.start_page_number,
                end_page_number=args.end_page_number,
                timeout=args.timeout,
                print_new=args.print_new,
                print_changes=args.print_changes,
                on_progress=report_progress,
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
        if progress_events % args.progress_every:
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
        if missing_fund_primary_key_store is not None:
            missing_fund_primary_key_store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
