# asof-store

An in-memory versioned store for retrieving the latest value written at or
before a specified timestamp.

## Install

```python
python -m pip install asof-store
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

SQLite and PostgreSQL URIs are supported through SQLAlchemy:

```python
sqlite_store = AsOfStore.from_sql("sqlite:///asof.db")
postgres_store = AsOfStore.from_sql(
    "postgresql+psycopg://user:password@localhost/database"
)
```

The SQL backend persists keys, timestamps, and values using Python pickle.
Only use it with databases you trust, since loading a database containing
untrusted pickle data can execute code. Objects written to the SQL backend
must be pickleable, and timestamps must support ordering comparisons. Call
`store.close()` when finished to release SQLAlchemy's pooled connections.
