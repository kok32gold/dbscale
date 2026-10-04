"""Adapter registry: maps a ``database.type`` string to an adapter class."""

from __future__ import annotations

from dbscale.adapters.base import AdapterError, DatabaseAdapter

_REGISTRY: dict[str, type[DatabaseAdapter]] = {}


def register_adapter(adapter_cls: type[DatabaseAdapter]) -> type[DatabaseAdapter]:
    """Register an adapter class (usable as a decorator)."""
    key = adapter_cls.type.lower()
    _REGISTRY[key] = adapter_cls
    return adapter_cls


def get_adapter(db_type: str) -> type[DatabaseAdapter]:
    key = db_type.lower()
    aliases = {"postgresql": "postgres", "pg": "postgres"}
    key = aliases.get(key, key)
    try:
        return _REGISTRY[key]
    except KeyError:
        raise AdapterError(
            f"No adapter for database type '{db_type}'. Available: {', '.join(sorted(_REGISTRY)) or 'none'}"
        ) from None


def available_adapters() -> list[str]:
    return sorted(_REGISTRY)
