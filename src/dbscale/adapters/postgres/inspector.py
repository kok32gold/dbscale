"""Read the PostgreSQL catalog into the normalized schema model.

Everything here is read-only. Statistics come from ``pg_stats`` but only the
*shape* is kept (fractions, counts, widths). ``most_common_vals`` is read only
when ``sample_common_values`` is set, and only for low-cardinality columns.
"""

from __future__ import annotations

import fnmatch
from typing import Any

import psycopg

from dbscale.core.schema import Column, ColumnStats, DataType, ForeignKey, Index, Schema, Table

_TYPE_MAP: dict[str, DataType] = {
    "int2": DataType.SMALLINT,
    "int4": DataType.INTEGER,
    "int8": DataType.BIGINT,
    "oid": DataType.BIGINT,
    "numeric": DataType.DECIMAL,
    "money": DataType.DECIMAL,
    "float4": DataType.FLOAT,
    "float8": DataType.FLOAT,
    "bool": DataType.BOOLEAN,
    "text": DataType.TEXT,
    "name": DataType.TEXT,
    "citext": DataType.TEXT,
    "varchar": DataType.VARCHAR,
    "bpchar": DataType.CHAR,
    "uuid": DataType.UUID,
    "timestamp": DataType.TIMESTAMP,
    "timestamptz": DataType.TIMESTAMPTZ,
    "date": DataType.DATE,
    "time": DataType.TIME,
    "timetz": DataType.TIME,
    "interval": DataType.INTERVAL,
    "json": DataType.JSON,
    "jsonb": DataType.JSON,
    "bytea": DataType.BINARY,
    "inet": DataType.NETWORK,
    "cidr": DataType.NETWORK,
    "macaddr": DataType.NETWORK,
}

_COUNT_TIMEOUT_MS = 5000

_TABLES_SQL = """
SELECT n.nspname, c.relname, c.oid, c.reltuples::float8, c.relkind
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND NOT c.relispartition
  AND n.nspname = ANY(%s)
ORDER BY n.nspname, c.relname
"""

_COLUMNS_SQL = """
SELECT a.attnum,
       a.attname,
       format_type(a.atttypid, a.atttypmod) AS native_type,
       format_type(t.oid, NULL) AS regtype_name,
       t.oid AS type_oid,
       t.typname,
       t.typtype,
       t.typcategory,
       t.typbasetype,
       t.typtypmod,
       a.atttypmod,
       a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid) AS default_expr,
       a.attidentity,
       a.attgenerated,
       et.oid AS elem_oid,
       et.typname AS elem_typname,
       et.typtype AS elem_typtype,
       et.typcategory AS elem_typcategory
FROM pg_attribute a
JOIN pg_type t ON t.oid = a.atttypid
LEFT JOIN pg_type et ON et.oid = t.typelem AND t.typcategory = 'A'
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

_CONSTRAINTS_SQL = """
SELECT c.conname, c.contype, c.conkey::int[], c.confrelid, c.confkey::int[],
       pc.relname AS ref_table, pn.nspname AS ref_schema
FROM pg_constraint c
LEFT JOIN pg_class pc ON pc.oid = c.confrelid
LEFT JOIN pg_namespace pn ON pn.oid = pc.relnamespace
WHERE c.conrelid = %s AND c.contype IN ('p', 'f', 'u')
ORDER BY c.conname
"""

_ATTNAMES_SQL = """
SELECT attnum, attname FROM pg_attribute WHERE attrelid = %s AND attnum = ANY(%s)
"""

_INDEXES_SQL = """
SELECT ic.relname AS index_name,
       i.indisunique,
       i.indisprimary,
       i.indkey::int[] AS indkey,
       am.amname,
       pg_get_indexdef(i.indexrelid) AS definition,
       pg_get_expr(i.indpred, i.indrelid) AS predicate,
       (i.indexprs IS NOT NULL) AS has_expressions
FROM pg_index i
JOIN pg_class ic ON ic.oid = i.indexrelid
JOIN pg_am am ON am.oid = ic.relam
WHERE i.indrelid = %s
ORDER BY ic.relname
"""

_STATS_SQL = """
SELECT attname, null_frac, n_distinct, avg_width, most_common_freqs, correlation, NULL::text[]
FROM pg_stats
WHERE schemaname = %s AND tablename = %s
"""

# Only used when the user opts in (database.sample_common_values). Reads actual values.
_STATS_WITH_VALUES_SQL = """
SELECT attname, null_frac, n_distinct, avg_width, most_common_freqs, correlation,
       CASE WHEN n_distinct BETWEEN 0 AND %s THEN most_common_vals::text::text[] END
FROM pg_stats
WHERE schemaname = %s AND tablename = %s
"""
_COMMON_VALUES_MAX_DISTINCT = 200

_ENUM_SQL = "SELECT enumlabel FROM pg_enum WHERE enumtypid = %s ORDER BY enumsortorder"

_BASE_TYPE_SQL = """
SELECT t.oid, t.typname, t.typtype, t.typcategory, t.typbasetype, t.typtypmod,
       format_type(t.oid, %s) AS native_type,
       format_type(t.oid, NULL) AS regtype_name
FROM pg_type t WHERE t.oid = %s
"""


class PostgresInspector:
    def __init__(
        self,
        conn: psycopg.Connection,
        server_version: str | None = None,
        *,
        sample_common_values: bool = False,
    ):
        self.conn = conn
        self.server_version = server_version
        self.sample_common_values = sample_common_values
        self._enum_cache: dict[int, list[str]] = {}
        self._extensions: dict[int, str] = {}

    def inspect(
        self,
        schemas: list[str] | None = None,
        include_tables: list[str] | None = None,
        exclude_tables: list[str] | None = None,
    ) -> Schema:
        schemas = schemas or ["public"]
        self._extensions = self._extension_types()
        tables: list[Table] = []
        with self.conn.cursor() as cur:
            cur.execute(_TABLES_SQL, (schemas,))
            rows = cur.fetchall()
        for nspname, relname, oid, reltuples, relkind in rows:
            if not _selected(relname, nspname, include_tables, exclude_tables):
                continue
            tables.append(self._inspect_table(oid, nspname, relname, reltuples, relkind))
        return Schema(database_type="postgres", database_version=self.server_version, tables=tables)

    # ------------------------------------------------------------------ table

    def _inspect_table(self, oid: int, nspname: str, relname: str, reltuples: float, relkind: str) -> Table:
        columns, attnames = self._columns(oid)
        estimated = self._estimate_rows(nspname, relname, reltuples)
        stats = self._stats(nspname, relname, estimated or 0)
        for col in columns:
            if col.name in stats:
                col.stats = stats[col.name]
        pk, fks, uniques = self._constraints(oid, attnames)
        indexes = self._indexes(oid, attnames)
        metadata: dict[str, Any] = {}
        if relkind == "p":
            metadata["partitioned"] = True
        return Table(
            name=relname,
            schema_name=nspname,
            columns=columns,
            primary_key=pk,
            foreign_keys=fks,
            indexes=indexes,
            unique_constraints=uniques,
            estimated_rows=max(0, int(estimated or 0)),
            metadata=metadata,
        )

    def _estimate_rows(self, nspname: str, relname: str, reltuples: float) -> int | None:
        if reltuples is not None and reltuples > 0:
            return int(reltuples)
        # Never analyzed (-1 on PG14+) or reported empty: try a cheap exact count.
        try:
            with self.conn.transaction():
                with self.conn.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = {_COUNT_TIMEOUT_MS}")
                    cur.execute(f"SELECT count(*) FROM {_q(nspname)}.{_q(relname)}")
                    row = cur.fetchone()
                    return int(row[0]) if row else None
        except psycopg.Error:
            return None

    # ---------------------------------------------------------------- columns

    def _columns(self, oid: int) -> tuple[list[Column], dict[int, str]]:
        columns: list[Column] = []
        attnames: dict[int, str] = {}
        with self.conn.cursor() as cur:
            cur.execute(_COLUMNS_SQL, (oid,))
            rows = cur.fetchall()
        for (
            attnum,
            attname,
            native_type,
            regtype_name,
            type_oid,
            typname,
            typtype,
            typcategory,
            typbasetype,
            typtypmod,
            atttypmod,
            attnotnull,
            default_expr,
            _attidentity,
            attgenerated,
            elem_oid,
            elem_typname,
            elem_typtype,
            elem_typcategory,
        ) in rows:
            attnames[attnum] = attname
            # Domains: resolve to the base type so the sandbox does not need the domain.
            if typtype == "d" and typbasetype:
                type_oid, typname, typtype, typcategory, native_type, atttypmod, regtype_name = (
                    self._resolve_domain(typbasetype, typtypmod)
                )
            data_type = self._classify(typname, typtype, typcategory)
            enum_values = self._enum_labels(type_oid) if data_type == DataType.ENUM else None
            array_element = None
            extension_oid = type_oid
            if data_type == DataType.ARRAY and elem_typname:
                array_element = self._classify(elem_typname, elem_typtype, elem_typcategory)
                if array_element == DataType.ARRAY:
                    array_element = DataType.OTHER
                if elem_oid:
                    extension_oid = int(elem_oid)
            type_extension = self._extensions.get(int(type_oid)) or self._extensions.get(int(extension_oid))
            max_length = None
            precision = scale = None
            if typname in ("varchar", "bpchar") and atttypmod is not None and atttypmod > 4:
                max_length = atttypmod - 4
            if typname == "numeric" and atttypmod is not None and atttypmod > 4:
                precision = (atttypmod - 4) >> 16
                scale = (atttypmod - 4) & 0xFFFF
            is_generated = attgenerated == "s"
            columns.append(
                Column(
                    name=attname,
                    data_type=data_type,
                    native_type=native_type,
                    nullable=not attnotnull,
                    default=default_expr,
                    max_length=max_length,
                    numeric_precision=precision,
                    numeric_scale=scale,
                    enum_values=enum_values,
                    is_generated=is_generated,
                    regtype_name=regtype_name,
                    type_extension=type_extension,
                    array_element=array_element,
                )
            )
        return columns, attnames

    def _resolve_domain(self, base_oid: int, typtypmod: int) -> tuple[int, str, str, str, str, int, str]:
        with self.conn.cursor() as cur:
            cur.execute(_BASE_TYPE_SQL, (typtypmod, base_oid))
            row = cur.fetchone()
        if row is None:
            return base_oid, "text", "b", "S", "text", -1, "text"
        oid, typname, typtype, typcategory, typbasetype, nested_typmod, native_type, regtype_name = row
        if typtype == "d" and typbasetype:
            return self._resolve_domain(typbasetype, nested_typmod)
        return oid, typname, typtype, typcategory, native_type, typtypmod, regtype_name

    def _extension_types(self) -> dict[int, str]:
        """Map type OID → owning extension. Empty for built-in types."""
        sql = """
        SELECT d.objid, e.extname
        FROM pg_depend d
        JOIN pg_extension e ON e.oid = d.refobjid
        WHERE d.classid = 'pg_type'::regclass AND d.deptype = 'e'
        """
        found: dict[int, str] = {}
        with self.conn.cursor() as cur:
            cur.execute(sql)
            for oid, name in cur.fetchall():
                found.setdefault(int(oid), name)
        return found

    def _enum_labels(self, type_oid: int) -> list[str]:
        if type_oid not in self._enum_cache:
            with self.conn.cursor() as cur:
                cur.execute(_ENUM_SQL, (type_oid,))
                self._enum_cache[type_oid] = [r[0] for r in cur.fetchall()]
        return self._enum_cache[type_oid]

    @staticmethod
    def _classify(typname: str, typtype: str, typcategory: str) -> DataType:
        if typtype == "e":
            return DataType.ENUM
        if typcategory == "A":
            return DataType.ARRAY
        return _TYPE_MAP.get(typname, DataType.OTHER)

    # ------------------------------------------------------------ constraints

    def _constraints(
        self, oid: int, attnames: dict[int, str]
    ) -> tuple[list[str], list[ForeignKey], list[list[str]]]:
        pk: list[str] = []
        fks: list[ForeignKey] = []
        uniques: list[list[str]] = []
        with self.conn.cursor() as cur:
            cur.execute(_CONSTRAINTS_SQL, (oid,))
            rows = cur.fetchall()
            for conname, contype, conkey, confrelid, confkey, ref_table, _ref_schema in rows:
                cols = [attnames[k] for k in (conkey or []) if k in attnames]
                if contype == "p":
                    pk = cols
                elif contype == "u":
                    uniques.append(cols)
                elif contype == "f" and ref_table:
                    cur.execute(_ATTNAMES_SQL, (confrelid, list(confkey or [])))
                    ref_names: dict[Any, Any] = dict(cur.fetchall())
                    fks.append(
                        ForeignKey(
                            name=conname,
                            columns=cols,
                            referenced_table=ref_table,
                            referenced_columns=[ref_names[k] for k in confkey if k in ref_names],
                        )
                    )
        return pk, fks, uniques

    # ---------------------------------------------------------------- indexes

    def _indexes(self, oid: int, attnames: dict[int, str]) -> list[Index]:
        indexes: list[Index] = []
        with self.conn.cursor() as cur:
            cur.execute(_INDEXES_SQL, (oid,))
            for name, unique, primary, indkey, amname, definition, predicate, has_expr in cur.fetchall():
                indexes.append(
                    Index(
                        name=name,
                        columns=[attnames[k] for k in indkey if k > 0 and k in attnames],
                        unique=unique,
                        primary=primary,
                        method=amname,
                        predicate=predicate,
                        has_expressions=bool(has_expr),
                        definition=definition,
                    )
                )
        return indexes

    # ------------------------------------------------------------------ stats

    def _stats(self, nspname: str, relname: str, estimated_rows: int) -> dict[str, ColumnStats]:
        out: dict[str, ColumnStats] = {}
        with self.conn.cursor() as cur:
            if self.sample_common_values:
                cur.execute(_STATS_WITH_VALUES_SQL, (_COMMON_VALUES_MAX_DISTINCT, nspname, relname))
            else:
                cur.execute(_STATS_SQL, (nspname, relname))
            for attname, null_frac, n_distinct, avg_width, mcf, correlation, mcv in cur.fetchall():
                distinct: int | None
                if n_distinct is None:
                    distinct = None
                elif n_distinct >= 0:
                    distinct = int(n_distinct)
                else:
                    # Negative n_distinct is a fraction of the row count (e.g. -1 = all unique).
                    distinct = max(1, int(round(-float(n_distinct) * estimated_rows)))
                out[attname] = ColumnStats(
                    null_fraction=float(null_frac) if null_frac is not None else None,
                    distinct_count=distinct,
                    avg_width=int(avg_width) if avg_width is not None else None,
                    top_frequencies=[float(f) for f in mcf] if mcf else None,
                    correlation=float(correlation) if correlation is not None else None,
                    common_values=[str(v) for v in mcv] if mcv else None,
                )
        return out


def _selected(table: str, schema: str, include: list[str] | None, exclude: list[str] | None) -> bool:
    qualified = f"{schema}.{table}"
    if include:
        if not any(fnmatch.fnmatch(table, p) or fnmatch.fnmatch(qualified, p) for p in include):
            return False
    if exclude and any(fnmatch.fnmatch(table, p) or fnmatch.fnmatch(qualified, p) for p in exclude):
        return False
    return True


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'
