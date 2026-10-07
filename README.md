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

## Zapros middleware (optional)

Install the Zapros integration extra:

```sh
uv add "asof-store[zapros]"
```

The middleware supports synchronous and asynchronous Zapros clients. In
`building` mode it forwards requests to the network and records non-empty JSON
objects from successful (2xx) responses, keyed by the exact URL and request body.
The version timestamp is the time the response is recorded. In `as_of` mode it
returns the latest saved JSON response at or before the supplied timezone-aware
timestamp, without calling the network handler. A missing match raises
Zapros's `UnhandledRequestError`.

```python
from datetime import UTC, datetime

from asof_store import AsOfStore
from asof_store.zapros import ZaprosAsOfMiddleware
from zapros import Client, StdNetworkHandler

store = AsOfStore.from_memory()

with Client(
    handler=ZaprosAsOfMiddleware(
        StdNetworkHandler(),
        store,
        mode="building",
    )
) as client:
    client.get("https://api.example.com/items")

with Client(
    handler=ZaprosAsOfMiddleware(
        None,
        store,
        mode="as_of",
        as_of=datetime.now(UTC),
    )
) as client:
    response = client.get("https://api.example.com/items")
    print(response.json)
```

`RequestKey` is a hashable dictionary with `uri` and `request_body` fields; the
request body is parsed from JSON (or `None` when absent). The middleware stores
non-empty JSON objects as response values.

For SQL persistence, create a store with timestamp type `datetime`, key type
`RequestKey`, and value type `dict`.

## Development

Install the test dependency and run the suite with pytest:

```sh
uv sync --all-packages --all-extras
uv run pytest
```
