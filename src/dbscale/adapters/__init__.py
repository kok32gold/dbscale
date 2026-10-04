"""Database adapters.

The core talks to a ``DatabaseAdapter``; concrete databases implement it.
Importing this package registers the built-in adapters.
"""

# Built-in adapters register themselves on import.
from dbscale.adapters import postgres as _postgres  # noqa: E402,F401
from dbscale.adapters.base import (
    AdapterCapabilities,
    AdapterError,
    DatabaseAdapter,
    ExecutionResult,
    ExplainResult,
    ServerInfo,
)
from dbscale.adapters.registry import available_adapters, get_adapter, register_adapter

__all__ = [
    "AdapterCapabilities",
    "AdapterError",
    "DatabaseAdapter",
    "ExecutionResult",
    "ExplainResult",
    "ServerInfo",
    "available_adapters",
    "get_adapter",
    "register_adapter",
]
