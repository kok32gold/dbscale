"""Compile a ``GenerationPlan`` into PostgreSQL expressions over ``generate_series``.

Rows are produced *inside* the database:

    INSERT INTO t (a, b, c)
    SELECT <expr_a>, <expr_b>, <expr_c> FROM generate_series(1, N) AS g(i)

Expressions are immutable functions of the row number and the plan seed
(``hashint8``, not ``random()``). ``random()`` is volatile, so it forces a
single backend and blocks parallel loads. ``md5`` is avoided for the same
reason: at tens of millions of rows it dominates the insert.
"""

from __future__ import annotations

import hashlib
import math

from dbscale.adapters.postgres.ddl import q
from dbscale.core.schema import Column, DataType, Schema, Table
from dbscale.generation.plan import ColumnPlan, GenerationPlan, Picker, TablePlan, ValueKind, ValueSpec

ROW = "g.i"


def stable_salt(seed: int, *parts: object) -> int:
    """Positive bigint. Stable across processes (unlike ``hash()``)."""
    payload = ":".join(str(p) for p in (seed, *parts))
    digest = hashlib.sha256(payload.encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def unit_sql(row: str, salt: int) -> str:
    """Deterministic float in ``[0, 1)`` from ``row``. Immutable, so it can run on many backends."""
    mixed = f"hashint8((({row})::bigint) # ({int(salt)}::bigint))"
    return f"((({mixed} & 2147483647)::float8) / 2147483648.0)"


class SqlValueCompiler:
    def __init__(self, schema: Schema, plan: GenerationPlan, rows: dict[str, int]):
        self.schema = schema
        self.plan = plan
        self.rows = rows

    # ------------------------------------------------------------- public

    def row_cap(self, table_plan: TablePlan) -> int | None:
        """Max rows a composite key can represent (product of its radices), or None."""
        for cp in table_plan.columns:
            s = cp.spec
            if (
                s.kind == ValueKind.KEY_REF
                and s.picker == Picker.RADIX
                and s.radix
                and s.radix_base == "sequence"
            ):
                return math.prod(max(1, self.rows.get(r, 1)) for r in s.radix)
        return None

    def select_list(
        self, table: Table, table_plan: TablePlan, total_rows: int
    ) -> tuple[list[str], list[str]]:
        names: list[str] = []
        exprs: list[str] = []
        for cp in table_plan.columns:
            col = table.column(cp.name)
            if col is None or col.is_generated:
                continue
            names.append(q(cp.name))
            exprs.append(self.column_expr(table, col, cp, total_rows))
        return names, exprs

    def column_expr(self, table: Table, col: Column, cp: ColumnPlan, total_rows: int) -> str:
        expr = self._spec_expr(table, col, cp.spec, total_rows)
        if cp.spec.kind != ValueKind.NULL:
            expr = f"({expr})::{col.physical_type}"
        if cp.null_fraction > 0 and col.nullable:
            unit = self._unit(table.name, col.name, "null")
            expr = f"CASE WHEN {unit} < {cp.null_fraction:.6f} THEN NULL ELSE {expr} END"
        return expr

    # ------------------------------------------------------------ private

    def _salt(self, *parts: object) -> int:
        return stable_salt(self.plan.seed, *parts)

    def _unit(self, *parts: object) -> str:
        return unit_sql(ROW, self._salt(*parts))

    def _spec_expr(self, table: Table, col: Column, spec: ValueSpec, total_rows: int) -> str:
        k = spec.kind
        if k == ValueKind.KEY_REF:
            return self._key_ref(table, col, spec)
        if k == ValueKind.INTEGER:
            unit = self._unit(table.name, col.name, "int")
            lo = int(spec.min_value or 0)
            if spec.distinct:
                return f"{lo} + floor({unit} * {max(1, spec.distinct)})::bigint"
            hi = int(spec.max_value if spec.max_value is not None else lo + 1_000_000)
            return f"{lo} + floor({unit} * {max(1, hi - lo + 1)})::bigint"
        if k == ValueKind.DECIMAL:
            unit = self._unit(table.name, col.name, "dec")
            low = float(spec.min_value or 0)
            high = float(spec.max_value if spec.max_value is not None else 1_000_000)
            decimals = spec.decimals if spec.decimals is not None else 2
            return f"round(({low} + {unit} * {high - low})::numeric, {decimals})"
        if k == ValueKind.BOOLEAN:
            return f"{self._unit(table.name, col.name, 'bool')} < {spec.p_true:.4f}"
        if k == ValueKind.TEXT:
            return self._text(table, col, spec)
        if k == ValueKind.TIMESTAMP:
            return self._temporal(table, col, spec, total_rows)
        if k == ValueKind.DATE:
            return f"({self._temporal(table, col, spec, total_rows)})::date"
        if k == ValueKind.TIME:
            return f"TIME '00:00' + {self._unit(table.name, col.name, 'time')} * interval '24 hours'"
        if k == ValueKind.INTERVAL:
            return f"{self._unit(table.name, col.name, 'interval')} * interval '1 day'"
        if k == ValueKind.UUID:
            return f"lpad(to_hex((({ROW})::bigint) # ({self._salt(table.name, col.name, 'uuid')}::bigint)), 32, '0')::uuid"
        if k == ValueKind.JSON:
            mixed = f"hashint8((({ROW})::bigint) # ({self._salt(table.name, col.name, 'json')}::bigint))"
            return f"jsonb_build_object('id', {ROW}, 'value', ({mixed} & 127))"
        if k == ValueKind.BINARY:
            salt = self._salt(table.name, col.name, "bin")
            return f"decode(lpad(to_hex((({ROW})::bigint) # ({salt}::bigint)), 32, '0'), 'hex')"
        if k == ValueKind.ENUM:
            labels = spec.values or col.enum_values or []
            arr = ", ".join("'" + v.replace("'", "''") + "'" for v in labels)
            unit = self._unit(table.name, col.name, "enum")
            return f"(ARRAY[{arr}])[1 + floor({unit} * {max(1, len(labels))})::int]"
        if k == ValueKind.ARRAY and spec.element is not None:
            return f"ARRAY[{self._spec_expr(table, col, spec.element, total_rows)}]"
        if k == ValueKind.CHOICE:
            return self._choice(table, col, spec, total_rows)
        if k == ValueKind.CONSTANT:
            if spec.constant is None:
                if col.default and "nextval" not in col.default:
                    return col.default
                if col.data_type == DataType.ARRAY:
                    return "'{}'"
                return "NULL"
            return _literal(spec.constant)
        if k == ValueKind.NULL:
            return "NULL"
        raise ValueError(f"Unsupported value kind: {k}")

    def _choice(self, table: Table, col: Column, spec: ValueSpec, total_rows: int) -> str:
        """Weighted CASE. The draw is a hash of the row number, so it cannot be hoisted out of the insert."""
        values = spec.values or []
        weights = spec.weights or []
        unit = self._unit(table.name, col.name, "choice")
        branches = []
        cumulative = 0.0
        for value, weight in zip(values, weights, strict=False):
            cumulative += weight
            literal = "'" + str(value).replace("'", "''") + "'"
            branches.append(f"WHEN {unit} < {min(cumulative, 1.0):.6f} THEN {literal}")
        fallback_spec = ValueSpec(
            kind=spec.fallback or ValueKind.TEXT,
            distinct=spec.distinct,
            min_value=spec.min_value,
            max_value=spec.max_value,
            length=spec.length,
            values=col.enum_values,
        )
        fallback = self._spec_expr(table, col, fallback_spec, total_rows)
        return f"CASE {' '.join(branches)} ELSE {fallback} END"

    def _text(self, table: Table, col: Column, spec: ValueSpec) -> str:
        length = max(1, spec.length or 24)
        if col.max_length:
            length = min(length, col.max_length)
        if spec.distinct:
            unit = self._unit(table.name, col.name, "text")
            expr = f"'v' || (1 + floor({unit} * {spec.distinct})::int)::text"
            width = max(length, len(str(spec.distinct)) + 1) if not col.max_length else col.max_length
            return f"left({expr}, {width})"
        salt = self._salt(table.name, col.name, "text")
        hex16 = f"lpad(to_hex((({ROW})::bigint) # ({salt}::bigint)), 16, '0')"
        if length <= 16:
            return f"right({hex16}, {length})"
        reps = max(1, math.ceil(length / 16))
        return f"left(repeat({hex16}, {reps}), {length})"

    def _temporal(self, table: Table, col: Column, spec: ValueSpec, total_rows: int) -> str:
        span = max(1, spec.span_days)
        unit = self._unit(table.name, col.name, "time")
        if spec.ordered:
            return (
                f"now() - interval '{span} days' + ({ROW} - 1) * (interval '{span} days' / {max(1, total_rows)})"
                f" + {unit} * interval '1 hour'"
            )
        return f"now() - {unit} * interval '{span} days'"

    # ------------------------------------------------------------- keys

    def _key_ref(self, table: Table, col: Column, spec: ValueSpec) -> str:
        ref_table = self.schema.table(spec.ref_table or table.name) or table
        ref_col = ref_table.column(spec.ref_column or col.name) or col
        k = self._pick(table, col, spec, ref_table)
        return key_expr(ref_table, ref_col, k)

    def _pick(self, table: Table, col: Column, spec: ValueSpec, ref_table: Table) -> str:
        n_ref = max(1, self.rows.get(ref_table.name, 1))
        p = spec.picker
        if p == Picker.SEQUENCE:
            return ROW
        if p == Picker.UNIFORM:
            unit = self._unit(table.name, col.name, "fk", ref_table.name)
            return f"(1 + floor({unit} * {n_ref})::bigint)"
        if p == Picker.SKEWED:
            unit = self._unit(table.name, col.name, "fk", ref_table.name)
            return f"(1 + floor(power({unit}, {spec.skew:.3f}) * {n_ref})::bigint)"
        if p == Picker.SELF_REF:
            if col.nullable:
                unit = self._unit(table.name, col.name, "self")
                return f"(CASE WHEN {ROW} > 1 THEN 1 + floor({unit} * ({ROW} - 1))::bigint END)"
            return f"GREATEST(1, ({ROW} / 2))"
        if p == Picker.RADIX:
            radices = [max(1, self.rows.get(r, 1)) for r in (spec.radix or [])]
            pos = spec.radix_position or 0
            if spec.radix_base == "uniform":
                unit = self._unit(table.name, col.name, "radix")
                base = f"floor({unit} * {n_ref})::bigint"
            else:
                base = f"({ROW} - 1)"
            divisor = math.prod(radices[:pos]) if pos > 0 else 1
            radix = radices[pos] if pos < len(radices) else n_ref
            return f"(1 + (({base}) / {divisor}) % {radix})"
        raise ValueError(f"Unsupported picker: {p}")


def key_expr(ref_table: Table, ref_col: Column, k: str) -> str:
    """Deterministically derive a key value of the right type from integer ``k``.

    Parents and children use the same function, so a child's foreign key value
    always matches an existing parent key without any lookup.
    """
    dt = ref_col.data_type
    if dt.is_integer or dt == DataType.DECIMAL or dt == DataType.FLOAT:
        return f"({k})"
    if dt == DataType.UUID:
        return f"lpad(to_hex(({k})::bigint), 32, '0')::uuid"
    if dt.is_textual:
        if ref_col.max_length and ref_col.max_length < 12:
            return f"({k})::text"
        return f"'{ref_col.name}-' || ({k})::text"
    return f"({k})::text"


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"
