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

Native SQL types are used where supported, including JSONB for `dict` and
`list` on PostgreSQL. Other Python types use pickle. SQL stores validate writes
against the declared types. SQL-backed `datetime` values must be timezone-aware.
Use a distinct table name for each type combination; an existing table is not
altered if its schema differs. Other Python values are stored with pickle, so
only use SQL stores with databases you trust, since loading a database
containing untrusted pickle data can execute code. Call `store.close()` when
finished to release SQLAlchemy's pooled connections.
SQL timestamps must use sortable scalar types supported by the database (such
as integers, strings, dates, datetimes, decimals, and UUIDs); compound JSON and
arbitrary pickle-backed timestamps are rejected. Boolean timestamps are not
supported because PostgreSQL does not provide ordering comparisons for them.
SQL stores index `(key, timestamp)` for efficient latest-version lookups.

## Interactive Brokers data (optional)

Install the Zapros extra to query the Interactive Brokers UK web API:

```sh
uv add "asof-store[zapros]"
```

Functions in `asof_store.ib` create and close a Zapros client when one is not
provided. Pass an existing sync or async client to reuse its connection.
`get_exchanges` fetches the exchange catalogue, while `get_instrument_summary`
fetches product counts by type. `get_products_by_filters` fetches one page, and
`scrape_instruments` fetches every reported page and yields instruments.
Async equivalents have an `_async` suffix; `scrape_instruments_async` is an
async iterator.

```python
from asof_store.ib import get_exchanges, scrape_instruments

exchanges = get_exchanges()
for instrument in scrape_instruments(page_size=500):
    print(instrument)
```

```python
import asyncio

from asof_store.ib import get_exchanges_async, scrape_instruments_async


async def main():
    exchanges = await get_exchanges_async()
    async for instrument in scrape_instruments_async(page_size=500):
        print(instrument)


asyncio.run(main())
```

Requests use the live IB API and are not cached or persisted. Product page
sizes must be 100, 200, 300, 400, or 500.

## Development

Install the test dependency and run the suite with pytest:

```sh
uv sync --all-packages --all-extras
uv run pytest
```
