"""PostgreSQL adapter."""

from dbscale.adapters.postgres.adapter import PostgresAdapter
from dbscale.adapters.registry import register_adapter

register_adapter(PostgresAdapter)

__all__ = ["PostgresAdapter"]
