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
requested timestamp. Timestamps for a key may be inserted in any order, and
writing the same key at the same timestamp replaces its value.

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

## Development

Install the test dependency and run the suite with pytest:

```sh
uv sync --all-packages --all-extras
uv run pytest
```
