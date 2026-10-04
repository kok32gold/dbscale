"""Reproduce a normalized schema as PostgreSQL DDL inside the sandbox.

Deliberate simplifications (documented in docs/databases/postgres.md):
* defaults, identity and sequences are dropped: every column is generated explicitly
* domains are replaced by their base type
* partitioned tables become regular tables
* composite UNIQUE constraints the generator cannot guarantee become plain indexes
"""

from __future__ import annotations

import re

from dbscale.core.schema import DataType, Schema, Table
from dbscale.generation.plan import GenerationPlan

_UNIQUE_RE = re.compile(r"^\s*CREATE\s+UNIQUE\s+INDEX\s+", re.I)
_CREATE_INDEX_RE = re.compile(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+", re.I)


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def qualified(table: Table) -> str:
    return f"{q(table.schema_name)}.{q(table.name)}" if table.schema_name else q(table.name)


def schema_statements(schema: Schema) -> list[str]:
    names = sorted({t.schema_name for t in schema.tables if t.schema_name and t.schema_name != "public"})
    return [f"CREATE SCHEMA IF NOT EXISTS {q(n)}" for n in names]


def enum_statements(schema: Schema) -> list[str]:
    seen: dict[str, list[str]] = {}
    for table in schema.tables:
        for col in table.columns:
            if col.data_type == DataType.ENUM and col.enum_values is not None:
                seen.setdefault(col.native_type, col.enum_values)
    stmts = []
    for native, labels in seen.items():
        values = ", ".join("'" + v.replace("'", "''") + "'" for v in labels)
        stmts.append(
            f"DO $$ BEGIN CREATE TYPE {native} AS ENUM ({values}); EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
    return stmts


def create_table_statement(table: Table, *, unlogged: bool = True) -> str:
    parts: list[str] = []
    for col in table.columns:
        piece = f"{q(col.name)} {col.physical_type}"
        if col.is_generated and col.default:
            piece += f" GENERATED ALWAYS AS ({col.default}) STORED"
        if not col.nullable:
            piece += " NOT NULL"
        parts.append(piece)
    if table.primary_key:
        cols = ", ".join(q(c) for c in table.primary_key)
        parts.append(f"CONSTRAINT {q(primary_key_name(table))} PRIMARY KEY ({cols})")
    kind = "UNLOGGED TABLE" if unlogged else "TABLE"
    return f"CREATE {kind} {qualified(table)} (\n  " + ",\n  ".join(parts) + "\n)"


def primary_key_name(table: Table) -> str:
    for ix in table.indexes:
        if ix.primary and ix.name:
            return ix.name
    return f"{table.name}_pkey"


def _rel_format(table: Table) -> str:
    """``format(...)`` call that yields this table's regclass, with literals escaped."""
    if table.schema_name:
        return f"format('%I.%I', {_sql_str(table.schema_name)}, {_sql_str(table.name)})"
    return f"format('%I', {_sql_str(table.name)})"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def drop_primary_key_statement(table: Table) -> str | None:
    """Drop the primary key if one exists. Empty when the table has no key columns."""
    if not table.primary_key:
        return None
    rel = _rel_format(table)
    return (
        "DO $$\n"
        "DECLARE cname name;\n"
        "BEGIN\n"
        "  SELECT conname INTO cname FROM pg_constraint\n"
        f"  WHERE conrelid = {rel}::regclass AND contype = 'p';\n"
        "  IF cname IS NOT NULL THEN\n"
        f"    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', {_sql_str(qualified(table))}, cname);\n"
        "  END IF;\n"
        "END $$"
    )


def add_primary_key_statement(table: Table) -> str | None:
    """Add the primary key when it is missing. Safe to run after a load that dropped it."""
    if not table.primary_key:
        return None
    rel = _rel_format(table)
    cols = ", ".join(q(c) for c in table.primary_key)
    return (
        "DO $$\n"
        "BEGIN\n"
        "  IF NOT EXISTS (\n"
        "    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conrelid = {rel}::regclass AND contype = 'p'\n"
        "  ) THEN\n"
        f"    ALTER TABLE {qualified(table)} ADD CONSTRAINT {q(primary_key_name(table))} "
        f"PRIMARY KEY ({cols});\n"
        "  END IF;\n"
        "END $$"
    )


def index_statements(table: Table, plan: GenerationPlan) -> list[str]:
    table_plan = plan.table(table.name)
    stmts: list[str] = []
    for ix in table.indexes:
        if ix.primary or ix.skipped_reason or not ix.definition:
            continue
        definition = ix.definition
        if ix.unique:
            unique_safe = (
                table_plan is not None
                and not ix.has_expressions
                and not ix.predicate
                and table_plan.is_unique_safe(ix.columns)
            )
            if not unique_safe:
                definition = _UNIQUE_RE.sub("CREATE INDEX ", definition)
        definition = _CREATE_INDEX_RE.sub(
            lambda m: f"CREATE {m.group(1) or ''}INDEX IF NOT EXISTS ", definition
        )
        stmts.append(definition)
    return stmts


def drop_index_statements(table: Table) -> list[str]:
    stmts = []
    for ix in table.indexes:
        if ix.primary:
            continue
        name = f"{q(table.schema_name)}.{q(ix.name)}" if table.schema_name else q(ix.name)
        stmts.append(f"DROP INDEX IF EXISTS {name}")
    return stmts


def foreign_key_statements(table: Table, schema: Schema) -> list[str]:
    stmts = []
    for fk in table.foreign_keys:
        parent = schema.table(fk.referenced_table)
        if parent is None:
            continue
        stmts.append(
            f"ALTER TABLE {qualified(table)} ADD CONSTRAINT {q(fk.name)} FOREIGN KEY ("
            + ", ".join(q(c) for c in fk.columns)
            + f") REFERENCES {qualified(parent)} ("
            + ", ".join(q(c) for c in fk.referenced_columns)
            + ") NOT VALID"
        )
    return stmts


def drop_foreign_key_statements(table: Table) -> list[str]:
    return [
        f"ALTER TABLE {qualified(table)} DROP CONSTRAINT IF EXISTS {q(fk.name)}" for fk in table.foreign_keys
    ]


def truncate_statement(schema: Schema) -> str:
    return "TRUNCATE " + ", ".join(qualified(t) for t in schema.tables) + " CASCADE"
