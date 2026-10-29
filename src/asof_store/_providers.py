from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from typing import Any
from uuid import UUID

from service_registrar import Provider, ProviderRegistry

from ._abc import AsOfStore

_TYPE_NAMES: dict[str, type[Any]] = {
    "bool": bool,
    "bytes": bytes,
    "date": date,
    "datetime": datetime,
    "Decimal": Decimal,
    "dict": dict,
    "float": float,
    "int": int,
    "list": list,
    "NoneType": type(None),
    "str": str,
    "tuple": tuple,
    "UUID": UUID,
}


def _configured_type(config: Mapping[str, object], name: str) -> type[Any]:
    value = config[name]
    if isinstance(value, type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"SQL provider config {name!r} must be a type or type name")

    python_type = _TYPE_NAMES.get(value)
    if python_type is not None:
        return python_type

    module_name, separator, qualified_name = value.partition(":")
    if not separator or not module_name or not qualified_name:
        supported = ", ".join(sorted(_TYPE_NAMES))
        raise ValueError(
            f"Unknown SQL provider type {value!r}; use one of {supported} "
            "or a 'module:Type' path"
        )
    configured_object: object = import_module(module_name)
    for part in qualified_name.split("."):
        configured_object = getattr(configured_object, part)
    if not isinstance(configured_object, type):
        raise TypeError(f"SQL provider config {name!r} does not resolve to a type")
    return configured_object


def _create_memory(
    *,
    config: Mapping[str, object],
    registry: ProviderRegistry,
) -> AsOfStore[Any, Any, Any]:
    from ._memory import MemoryBackend

    return MemoryBackend()


def _create_sql(
    *,
    config: Mapping[str, object],
    registry: ProviderRegistry,
) -> AsOfStore[Any, Any, Any]:
    try:
        from ._sql import SqlBackend
    except ModuleNotFoundError as exc:
        if exc.name != "polars":
            raise
        raise ImportError(
            "SQL storage requires optional dependencies; install "
            "'asof-store[sql-sqlite]' for SQLite or "
            "'asof-store[sql-postgres]' for PostgreSQL"
        ) from exc

    try:
        sql_uri = config["sql_uri"]
        table_name = config["table_name"]
    except KeyError as exc:
        raise ValueError(
            f"SQL provider config is missing required setting {exc.args[0]!r}"
        ) from exc
    if not isinstance(sql_uri, str):
        raise TypeError("SQL provider config 'sql_uri' must be a string")
    if not isinstance(table_name, str):
        raise TypeError("SQL provider config 'table_name' must be a string")

    return SqlBackend(
        sql_uri,
        table_name,
        _configured_type(config, "timestamp_type"),
        _configured_type(config, "key_type"),
        _configured_type(config, "value_type"),
    )


MEMORY_PROVIDER = Provider(service=AsOfStore, factory=_create_memory)
SQL_PROVIDER = Provider(service=AsOfStore, factory=_create_sql)

__all__ = ["MEMORY_PROVIDER", "SQL_PROVIDER"]
