"""Make an inspected schema runnable on a sandbox that does not have every type.

PostgreSQL fails ``CREATE TABLE`` when a type name does not resolve (``vector``,
``geometry``, a composite, …). The sandbox image often lacks the extension that
defined it. This module:

1. tries ``CREATE EXTENSION`` for every extension the source schema uses
2. asks the sandbox which types and index methods actually exist
3. rewrites columns the sandbox cannot store, or that DBScale cannot fill, to ``text``
4. drops generated expressions and indexes that depend on those columns

The source ``native_type`` is kept for reporting. DDL and casts use ``sandbox_type``.
"""

from __future__ import annotations

import re

import psycopg
from psycopg import sql as pgsql

from dbscale.core.schema import Column, DataType, Index, Schema, Table


def prepare_sandbox(conn: psycopg.Connection, schema: Schema) -> list[str]:
    """Install what we can, then rewrite ``schema`` in place. Returns new fidelity notes."""
    if _already_adapted(schema):
        return []
    failed_extensions = _install_extensions(conn, _needed_extensions(schema))
    present_types = _present_types(conn, _regtype_names(schema))
    present_methods = _present_methods(conn)
    return adapt_schema(
        schema,
        present_types=present_types,
        present_methods=present_methods,
        failed_extensions=failed_extensions,
    )


def adapt_schema(
    schema: Schema,
    *,
    present_types: set[str],
    present_methods: set[str],
    failed_extensions: dict[str, str] | None = None,
) -> list[str]:
    """Pure rewrite. ``present_types`` holds ``regtype_name`` values the sandbox resolved.

    Columns with no ``regtype_name`` (hand-built schemas, older snapshots) keep a
    generatable built-in type and degrade everything else.
    """
    if _already_adapted(schema):
        return []
    failed_extensions = failed_extensions or {}
    notes: list[str] = []
    for ext in sorted(failed_extensions):
        notes.append(f"extension {ext}: not installed in the sandbox ({failed_extensions[ext]})")

    for table in schema.tables:
        for col in table.columns:
            _adapt_column(table, col, present_types, notes)
        for col in table.columns:
            degraded_now = {c.name for c in table.columns if c.degraded_reason}
            if not (col.is_generated and col.default and _mentions_any(col.default, degraded_now)):
                continue
            col.is_generated = False
            col.default = None
            if col.degraded_reason is None and not _can_fill(col):
                _mark_degraded(
                    table,
                    col,
                    f"generated expression on {col.native_type} depends on a column stored as text; stored as text",
                    notes,
                )
            elif col.degraded_reason is None:
                notes.append(
                    f"{table.name}.{col.name}: generated expression dropped; it depends on a column stored as text"
                )
        degraded = {c.name for c in table.columns if c.degraded_reason}
        for ix in table.indexes:
            reason = _index_skip_reason(ix, degraded, present_methods)
            if reason is None:
                continue
            ix.skipped_reason = reason
            notes.append(f"{table.name}.{ix.name}: index skipped ({reason})")
    return notes


def _adapt_column(table: Table, col: Column, present_types: set[str], notes: list[str]) -> None:
    if _type_available(col, present_types) and _can_fill(col):
        col.sandbox_type = col.native_type
        return
    if not _type_available(col, present_types):
        reason = f"type {col.native_type} is not available in the sandbox; stored as text"
    else:
        reason = f"no synthetic generator for {col.native_type}; stored as text"
    _mark_degraded(table, col, reason, notes)


def _mark_degraded(table: Table, col: Column, reason: str, notes: list[str]) -> None:
    col.sandbox_type = "text"
    col.degraded_reason = reason
    col.is_generated = False
    col.default = None
    notes.append(f"{table.name}.{col.name}: {reason}")


def _can_fill(col: Column) -> bool:
    """True when INSERT or a generated expression can satisfy the column without a cast guess."""
    if col.can_synthesize():
        return True
    if col.is_generated and col.default:
        return True
    if col.nullable:
        return True
    return bool(col.default)


def _type_available(col: Column, present_types: set[str]) -> bool:
    # Enums are created in the sandbox before tables, so they are not in pg_type yet.
    if col.data_type == DataType.ENUM:
        return True
    if col.regtype_name is None:
        return col.can_synthesize()
    return col.regtype_name in present_types


def _index_skip_reason(ix: Index, degraded: set[str], present_methods: set[str]) -> str | None:
    if ix.primary or ix.skipped_reason:
        return None
    used = [name for name in ix.columns if name in degraded]
    if not used and _mentions_any(ix.definition, degraded):
        used = sorted(name for name in degraded if ix.definition and _mentions(ix.definition, name))
    if not used and _mentions_any(ix.predicate, degraded):
        used = sorted(name for name in degraded if ix.predicate and _mentions(ix.predicate, name))
    if used:
        shown = ", ".join(used)
        return f"column {shown} stored as text"
    if ix.method and ix.method not in present_methods:
        return f"index method {ix.method} is not available"
    return None


def _mentions_any(sql: str | None, names: set[str]) -> bool:
    if not sql or not names:
        return False
    return any(_mentions(sql, name) for name in names)


def _mentions(sql: str, name: str) -> bool:
    return re.search(rf'(?<![A-Za-z0-9_])"?{re.escape(name)}"?(?![A-Za-z0-9_])', sql) is not None


def _already_adapted(schema: Schema) -> bool:
    columns = [col for table in schema.tables for col in table.columns]
    return bool(columns) and all(col.sandbox_type is not None for col in columns)


def _needed_extensions(schema: Schema) -> list[str]:
    names = {
        col.type_extension
        for table in schema.tables
        for col in table.columns
        if col.type_extension
    }
    return sorted(names)


def _regtype_names(schema: Schema) -> list[str]:
    names = {
        col.regtype_name
        for table in schema.tables
        for col in table.columns
        if col.regtype_name and col.data_type != DataType.ENUM
    }
    return sorted(names)


def _install_extensions(conn: psycopg.Connection, names: list[str]) -> dict[str, str]:
    failed: dict[str, str] = {}
    with conn.cursor() as cur:
        for name in names:
            try:
                cur.execute(pgsql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(pgsql.Identifier(name)))
            except psycopg.Error as exc:
                message = str(exc).strip().splitlines()
                failed[name] = message[0] if message else "unavailable"
    return failed


def _present_types(conn: psycopg.Connection, names: list[str]) -> set[str]:
    found: set[str] = set()
    with conn.cursor() as cur:
        for name in names:
            cur.execute("SELECT to_regtype(%s) IS NOT NULL", (name,))
            row = cur.fetchone()
            if row and row[0]:
                found.add(name)
    return found


def _present_methods(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT amname FROM pg_am")
        return {row[0] for row in cur.fetchall()}
