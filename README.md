# asof-store

An in-memory versioned store for retrieving the latest value written at or
before a specified timestamp.

## Install

```sh
uv add asof-store
```

## Usage

```python
from asof_store import AsOfStore

store = AsOfStore.from_memory()
store.put(as_of=10, key="price", value=100)
store.put(as_of=20, key="price", value=125)

assert store.get(as_of=15, key="price") == 100

with store.as_of(20) as snapshot:
    assert snapshot.get("price") == 125
```

Both `get` methods return `None` when no value exists at or before the
requested timestamp. For each key, writes must use strictly increasing
timestamps. `put` returns `False` without recording a new version when the
value equals that key's latest value, or when the exact latest
`(timestamp, key, value)` is repeated. Otherwise, it records the value and
returns `True`. Reusing a timestamp with a different value or going back to an
earlier timestamp raises `ValueError`.

## SQL storage (optional)

Install the optional extra matching your database:

```sh
python -m pip install "asof-store[sql-sqlite]"
# or, for PostgreSQL:
python -m pip install "asof-store[sql-postgres]"
```

Pass the table name and timestamp, key, and value types when creating a
SQL-backed store:

```python
from asof_store import AsOfStore

sqlite_store = AsOfStore.from_sql(
    "sqlite:///asof.db", "price_versions", int, str, dict
)
postgres_store = AsOfStore.from_sql(
    "postgresql+psycopg://user:password@localhost/database",
    "price_versions",
    int,
    str,
    dict,
)
```

Native SQL types are used where supported, including JSONB for `dict`, `list`,
`tuple`, and Pydantic `BaseModel` subclasses on PostgreSQL (JSON on SQLite).
Tuples are stored as JSON arrays and restored as tuples. Pydantic models are
serialized with `model_dump(mode="json", exclude_unset=True)` and restored to
their declared model type when read. Other Python types use pickle. SQL stores
validate writes against the declared types. SQL-backed `datetime` values must
be timezone-aware.
Use a distinct table name for each type combination; an existing table is not
altered if its schema differs. Other Python values are stored with pickle, so
only use SQL stores with databases you trust, since loading a database
containing untrusted pickle data can execute code. Call `store.close()` when
finished to release SQLAlchemy's pooled connections.
SQL timestamps must use sortable scalar types supported by the database (such
as integers, strings, dates, datetimes, decimals, and UUIDs); compound JSON and
arbitrary pickle-backed timestamps are rejected. Boolean timestamps are not
supported because PostgreSQL does not provide ordering comparisons for them.
SQL tables use `(key, timestamp)` as a composite primary key, which also
provides the index used for efficient latest-version lookups.

## Interactive Brokers data (optional)

Install the Zapros extra to query the Interactive Brokers UK web API:

```sh
uv add "asof-store[scraper]"
```

Functions in `asof_store.ib` create and close a Zapros client when one is not
provided. Pass an existing sync or async client to reuse its connection.
`get_exchanges` fetches the exchange catalogue, while `get_instrument_summary`
fetches product counts by type. `get_products_by_filters` fetches one page, and
`scrape_instruments` fetches every reported page and yields instruments.
`get_funds_by_filters` and `scrape_funds` use IB's dedicated funds endpoint.
Async equivalents have an `_async` suffix; `scrape_instruments_async` is an
async iterator.

The API returns Pydantic models: `ExchangeResponse`, `InstrumentSummaryItem`,
`ProductsResponse`, `Instrument`, and `Fund`. Request models
`InstrumentSummaryRequest`, `ProductsByFiltersRequest`, and
`FundProductsRequest` serialize field names to the format expected by IB.
Instrument and fund models allow additional IB fields so new or
product-specific response fields are retained. `Instrument.conid` and
`Fund.conid` are IB contract IDs and are used as their primary keys. Instruments
or funds without a contract ID have `primary_key == None`. Exchanges use the
composite primary key `(id, country_code)`.

```python
from asof_store.ib import InstrumentScrapeSummary, get_exchanges, scrape_instruments

exchanges = get_exchanges()
for item in scrape_instruments(page_size=500):
    if isinstance(item, InstrumentScrapeSummary):
        print("Summary:", item.products)
    else:
        print(item)

us_stocks = scrape_instruments(
    page_size=500,
    product_type=["STK"],
    product_country=["US"],
    new_product="T",
)
```

```python
import asyncio

from asof_store.ib import (
    InstrumentScrapeSummary,
    get_exchanges_async,
    scrape_instruments_async,
)


async def main():
    exchanges = await get_exchanges_async()
    async for item in scrape_instruments_async(page_size=500):
        if isinstance(item, InstrumentScrapeSummary):
            print("Summary:", item.products)
        else:
            print(item)

    async for instrument in scrape_instruments_async(
        product_type=["STK"],
        product_country=["US"],
        new_product="F",
    ):
        print(instrument)


asyncio.run(main())
```

`product_type` and `product_country` accept lists of IB product types and
countries. `new_product` accepts `"all"` (default), `"T"`, or `"F"` to select
all, new, or non-new products. These filters are sent to both the summary used
to determine pages and the products requests. Requests use the live IB API and
are not cached or persisted. Product page sizes must be 100, 200, 300, 400, or
500. Both instrument scrape iterators yield an `InstrumentScrapeSummary`
tagged event first (`event == "summary"`, with `products` containing
`InstrumentSummaryItem` values), before requesting product pages, followed by
`Instrument` objects and a page-complete event after each page. The persistence
API invokes `on_summary` before processing, then calls `on_progress` once for
each completed page with that page's instrument ordinal range.

### Live integration tests

Opt-in integration tests are kept in `tests/integration/`. The IB tests call the
real IB UK API, including each endpoint and the synchronous and asynchronous
scrapers. Install the Zapros and test extras, then enable those tests explicitly:

```powershell
uv sync --extra zapros --extra dev
$env:ASOF_STORE_RUN_IB_INTEGRATION = "1"
uv run pytest -m integration tests/integration/test_ib_live.py
```

The PostgreSQL tests use `postgresql://louis@fre.local/louis`. Install the
PostgreSQL and test extras, then enable them explicitly:

```powershell
uv sync --extra sql-postgres --extra dev
$env:ASOF_STORE_RUN_POSTGRES_INTEGRATION = "1"
uv run pytest -m integration tests/integration/test_asof_store_postgres.py
```

Without the corresponding environment variable, live integration tests are
skipped and the default test suite makes no live IB or PostgreSQL requests.
The live IB tests also exercise the dedicated funds endpoint.

### Scraping instruments into an as-of store

Install the scraper and SQL extras, then run the explicit scraper command:

```powershell
uv sync --extra scraper --extra sql-sqlite
uv run ass scrape-instruments --sql-uri "sqlite:///ib-instruments.db"
```

Use `--sql-uri` with `--table-name` (default `ib_instruments`) to choose the
destination. Optional repeated `--product-type` and `--product-country`
arguments filter the scrape; `--new-product` accepts `all`, `T`, or `F`.
`--start-page-number` resumes pagination at a given page and requires exactly
one `--product-type`; pages before it are not requested. `--end-page-number`
sets an inclusive last page and also requires exactly one `--product-type`.
`--print-new` prints each new instrument as JSON, and `--print-changes` prints
field-level JSON diffs for changed instruments. Progress is reported after
every page by default; `--progress-every` changes the number of page reports
between progress messages. Each message includes the product type and
instrument ordinal range, calculated from the page number and page size. The
final summary reports total and disjoint counts for new, changed, and unchanged
instruments with primary keys, plus new and unchanged instruments without
primary keys. Instruments without an IB `conid` are stored in a second
table, `ib_instrument_templates` by default, keyed by the complete
`Instrument` model; choose another table with
`--missing-primary-key-table-name`.

### Scraping funds into an as-of store

IB uses a dedicated funds scanner endpoint and returns a `total` count with
each page of funds. Scrape and persist those pages with:

```powershell
uv run ass scrape-funds --sql-uri "sqlite:///ib-funds.db"
```

The primary table defaults to `ib_funds`, keyed by each fund's `CONID`; funds
without a contract ID are stored in `ib_fund_templates` by default. Use
`--table-name` and `--missing-primary-key-table-name` to select different
tables. `--product-country` can be repeated, and `--product-symbol`,
`--new-product`, `--page-size`, `--start-page-number`, and `--end-page-number`
filter or limit the scrape. Progress is reported after each page by default;
`--progress-every` selects how many pages to skip between reports. New records
and changes can be printed with `--print-new` and `--print-changes`.

Scrape and store the exchange catalogue with:

```powershell
uv run ass scrape-exchanges --sql-uri "sqlite:///ib-exchanges.db"
```

Exchanges use `(id, country_code)` as their composite primary key. Exchanges
missing either component are stored in `ib_exchange_templates` by default;
use `--missing-primary-key-table-name` to select another table. The exchange
scraper also supports `--print-new`, `--print-changes`, and `--progress-every`.

The Python API provides `scrape_and_store_instruments(store, ...)` and
`async_scrape_and_store_instruments(store, ...)`. Both accept an instrument
store, plus a `missing_primary_key_store` of type
`AsOfStore[datetime, Instrument, NoneType]`, and return a `ScrapeReport`.
Pass `start_page_number` and `end_page_number` to resume or limit a
single-product-type scrape.
Use `scrape_and_store_exchanges(store, ...)` or
`async_scrape_and_store_exchanges(store, ...)` for exchanges, with an
`AsOfStore[datetime, tuple[str, str], Exchange]` and a separate
`AsOfStore[datetime, Exchange, NoneType]` for exchanges missing key components.
Use `scrape_and_store_funds(store, ...)` or
`async_scrape_and_store_funds(store, ...)` with an
`AsOfStore[datetime, int, Fund]` and a separate
`AsOfStore[datetime, Fund, NoneType]` for funds without a contract ID.

## Publishing

Releases published on GitHub are built and published to PyPI by
`.github/workflows/publish.yml`. Before the first release, configure a PyPI
Trusted Publisher for this GitHub repository with workflow
`publish.yml` and environment `pypi`. Create the matching `pypi` environment
in the repository's GitHub Actions settings.

To publish a new version, update the version in `pyproject.toml`, push the
change, and publish a GitHub Release for that version. The workflow runs the
test suite before building and publishing the package.

## Development

Install the development tools, tests, and optional dependencies with uv:

```sh
uv sync --all-packages --all-extras
uv run pytest
```

Install the Git commit hooks with `uv run prek install`. The hooks run Ruff
on changed Python files and check project types with ty when Python files
change. Run them manually with `uv run prek run --all-files`.
