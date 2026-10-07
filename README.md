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
