# asof-store

A versioned store for retrieving the latest value written at or
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
Passing `None` as `as_of` to `get` or `store.as_of(None)` selects the latest
available value, matching `get_all(None)` and `get_unique_set(field, None)`.

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
When `value_type` is `NoneType`, SQL tables omit the value column; the memory
backend does not allocate per-version value storage for `None` values.
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
All stores provide `get_all(as_of=None)` to return each key's latest value and
`get_unique_set(field, as_of=None)` to return distinct values of a mapping or
Pydantic model field from those latest values. Supplying `as_of` limits each
query to versions at or before that timestamp. Missing or null fields appear
as `None` in the set.

Use `get_all_matching(filters, as_of=None, exact=False)` to select mapping or
Pydantic values by string fields. Filters are combined with AND, matching is a
case-insensitive substring by default, and dotted names select nested mapping
fields. Set `exact=True` for case-insensitive exact matching. SQL stores apply
these filters before decoding result values; memory stores use the same matching
rules in Python. `AsOfView.get_all_matching(filters, exact=False)` applies the
view's fixed timestamp.

`get_keys_matching(filters, as_of=None, exact=False)` applies the same filtering
rules to JSON-compatible keys. This supports stores that retain records as keys
with `NoneType` values. `AsOfView.get_keys_matching(filters, exact=False)` uses
the view's fixed timestamp.

For fuzzy lookups, use `get_all_matching_terms(terms, fields, as_of=None,
exact=False)` or `get_keys_matching_terms(...)`. Every term must match at least
one listed field, and all terms must match. SQL backends combine these checks in
the database before decoding matching values or keys.

## PostgreSQL integration tests

The PostgreSQL integration tests use `postgresql://louis@fre.local/louis`.
Install the PostgreSQL and test extras, then enable them explicitly:

```powershell
uv sync --extra sql-postgres --extra dev
$env:ASOF_STORE_RUN_POSTGRES_INTEGRATION = "1"
uv run pytest -m integration tests/integration/test_asof_store_postgres.py
```

Without that environment variable, these tests are skipped and the default
test suite makes no live PostgreSQL requests.

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
uv sync --all-extras --locked
uv run pytest
```

Install the Git commit hooks with `uv run prek install`. The hooks run Ruff
on changed Python files and check project types with ty when Python files
change. Run them manually with `uv run prek run --all-files`.
