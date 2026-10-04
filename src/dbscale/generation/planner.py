"""Turn a schema (plus privacy-safe statistics) into a ``GenerationPlan``.

The planner preserves what matters for performance: key uniqueness, foreign
key relationships, cardinality, null fractions, skew and temporal ordering.
It never needs a single production row.
"""

from __future__ import annotations

import re

from dbscale.core.schema import Column, DataType, Schema, Table
from dbscale.generation.plan import ColumnPlan, GenerationPlan, Picker, TablePlan, ValueKind, ValueSpec

_TEMPORAL_ORDERED_RE = re.compile(
    r"(created|inserted|updated|modified|occurred|logged|_at$|_on$|timestamp|date)", re.I
)
_DEFAULT_NULL_FRACTION = 0.05
_SKEW_THRESHOLD = 0.2  # top-N values covering >20% of rows => treat parent references as skewed
_LOW_CARDINALITY_TEXT = 1000


class GenerationPlanner:
    def __init__(self, schema: Schema, seed: int = 42):
        self.schema = schema
        self.seed = seed
        self.notes: list[str] = []

    def plan(self) -> GenerationPlan:
        tables = [self._plan_table(t) for t in self.schema.topological_order()]
        return GenerationPlan(tables=tables, seed=self.seed, notes=self.notes)

    # ------------------------------------------------------------------ tables

    def _plan_table(self, table: Table) -> TablePlan:
        columns: list[ColumnPlan] = []
        guaranteed: list[list[str]] = []
        pk = table.primary_key
        if pk:
            guaranteed.append(list(pk))
        for col in table.columns:
            if col.is_generated:
                continue
            spec = self._plan_column(table, col)
            null_fraction = self._null_fraction(table, col)
            if spec.kind == ValueKind.KEY_REF and spec.picker in (Picker.SEQUENCE, Picker.RADIX):
                null_fraction = 0.0
            columns.append(ColumnPlan(name=col.name, spec=spec, null_fraction=null_fraction))
            if (
                spec.kind == ValueKind.KEY_REF
                and spec.picker == Picker.SEQUENCE
                and [col.name] not in guaranteed
            ):
                guaranteed.append([col.name])
        for uc in table.unique_constraints:
            if len(uc) > 1 and uc not in guaranteed:
                self.notes.append(
                    f"{table.name}: composite unique constraint on ({', '.join(uc)}) is reproduced as a non-unique index"
                )
        return TablePlan(table=table.name, columns=columns, guaranteed_unique=guaranteed)

    # ----------------------------------------------------------------- columns

    def _plan_column(self, table: Table, col: Column) -> ValueSpec:
        pk = table.primary_key
        fk = table.foreign_key_for(col.name)

        if col.name in pk and len(pk) > 1:
            return self._composite_key_component(table, col, pk, fk)
        if col.name in pk:
            return ValueSpec(
                kind=ValueKind.KEY_REF, ref_table=table.name, ref_column=col.name, picker=Picker.SEQUENCE
            )
        if fk is not None:
            return self._foreign_key(table, col, fk)
        if table.is_unique_column(col.name) and (
            col.data_type.is_integer or col.data_type.is_textual or col.data_type == DataType.UUID
        ):
            return ValueSpec(
                kind=ValueKind.KEY_REF, ref_table=table.name, ref_column=col.name, picker=Picker.SEQUENCE
            )
        return self._scalar(col)

    def _composite_key_component(self, table: Table, col: Column, pk: list[str], fk) -> ValueSpec:
        radix: list[str] = []
        for pk_col in pk:
            pk_fk = table.foreign_key_for(pk_col)
            radix.append(
                pk_fk.referenced_table if pk_fk and pk_fk.referenced_table != table.name else table.name
            )
        position = pk.index(col.name)
        if fk is not None and fk.referenced_table != table.name:
            ref_table, ref_column = fk.referenced_table, fk.referenced_columns[fk.columns.index(col.name)]
        else:
            ref_table, ref_column = table.name, col.name
        return ValueSpec(
            kind=ValueKind.KEY_REF,
            ref_table=ref_table,
            ref_column=ref_column,
            picker=Picker.RADIX,
            radix_position=position,
            radix=radix,
            radix_base="sequence",
        )

    def _foreign_key(self, table: Table, col: Column, fk) -> ValueSpec:
        ref_column = fk.referenced_columns[fk.columns.index(col.name)]
        parent = self.schema.table(fk.referenced_table)
        if fk.referenced_table == table.name:
            return ValueSpec(
                kind=ValueKind.KEY_REF, ref_table=table.name, ref_column=ref_column, picker=Picker.SELF_REF
            )
        if fk.is_composite and parent is not None and len(parent.primary_key) == len(fk.columns):
            # Choose one parent row uniformly, then decompose it exactly like the parent did.
            radix = []
            for pk_col in parent.primary_key:
                pk_fk = parent.foreign_key_for(pk_col)
                radix.append(
                    pk_fk.referenced_table if pk_fk and pk_fk.referenced_table != parent.name else parent.name
                )
            position = (
                parent.primary_key.index(ref_column)
                if ref_column in parent.primary_key
                else fk.columns.index(col.name)
            )
            return ValueSpec(
                kind=ValueKind.KEY_REF,
                ref_table=parent.name,
                ref_column=ref_column,
                picker=Picker.RADIX,
                radix_position=position,
                radix=radix,
                radix_base="uniform",
            )
        skew = 1.0
        if col.stats and col.stats.skew >= _SKEW_THRESHOLD:
            skew = 2.5
        return ValueSpec(
            kind=ValueKind.KEY_REF,
            ref_table=fk.referenced_table,
            ref_column=ref_column,
            picker=Picker.SKEWED if skew > 1 else Picker.UNIFORM,
            skew=skew,
        )

    def _scalar(self, col: Column) -> ValueSpec:
        if col.degraded_reason:
            # Sandbox column is text. Do not emit the source type, a source default, or NULL-only.
            return ValueSpec(kind=ValueKind.TEXT, length=8)
        dt = col.data_type
        stats = col.stats
        distinct = (
            stats.distinct_count if stats and stats.distinct_count and stats.distinct_count > 0 else None
        )

        choice = self._choice_from_common_values(col)
        if choice is not None:
            return choice
        if dt == DataType.BOOLEAN:
            p = 0.5
            if stats and stats.top_frequencies:
                p = max(0.01, min(0.99, stats.top_frequencies[0]))
            return ValueSpec(kind=ValueKind.BOOLEAN, p_true=p)
        if dt == DataType.SMALLINT:
            return ValueSpec(
                kind=ValueKind.INTEGER, min_value=0, max_value=32_000, distinct=_cap(distinct, 32_000)
            )
        if dt == DataType.INTEGER:
            return ValueSpec(
                kind=ValueKind.INTEGER,
                min_value=0,
                max_value=1_000_000,
                distinct=_cap(distinct, 2_000_000_000),
            )
        if dt == DataType.BIGINT:
            return ValueSpec(kind=ValueKind.INTEGER, min_value=0, max_value=1_000_000_000, distinct=distinct)
        if dt == DataType.DECIMAL:
            decimals = col.numeric_scale if col.numeric_scale is not None else 2
            precision = col.numeric_precision or 12
            max_value = min(10 ** max(precision - decimals - 1, 1), 1_000_000)
            return ValueSpec(
                kind=ValueKind.DECIMAL, min_value=0, max_value=max_value, decimals=decimals, distinct=distinct
            )
        if dt == DataType.FLOAT:
            return ValueSpec(
                kind=ValueKind.DECIMAL, min_value=0, max_value=1_000_000, decimals=4, distinct=distinct
            )
        if dt.is_textual:
            length = stats.avg_width if stats and stats.avg_width else 24
            if col.max_length:
                length = min(length, col.max_length)
            length = max(1, length)
            if distinct is not None and distinct <= _LOW_CARDINALITY_TEXT:
                return ValueSpec(kind=ValueKind.TEXT, distinct=distinct, length=length)
            return ValueSpec(kind=ValueKind.TEXT, distinct=None, length=length)
        if dt == DataType.UUID:
            return ValueSpec(kind=ValueKind.UUID)
        if dt in (DataType.TIMESTAMP, DataType.TIMESTAMPTZ):
            return ValueSpec(kind=ValueKind.TIMESTAMP, ordered=self._is_ordered(col))
        if dt == DataType.DATE:
            return ValueSpec(kind=ValueKind.DATE, ordered=self._is_ordered(col))
        if dt == DataType.TIME:
            return ValueSpec(kind=ValueKind.TIME)
        if dt == DataType.INTERVAL:
            return ValueSpec(kind=ValueKind.INTERVAL)
        if dt == DataType.JSON:
            return ValueSpec(kind=ValueKind.JSON)
        if dt == DataType.ARRAY and col.array_element is not None and col.can_synthesize():
            inner = col.model_copy(
                update={
                    "data_type": col.array_element,
                    "array_element": None,
                    "native_type": "text",
                    "sandbox_type": None,
                    "degraded_reason": None,
                    "is_generated": False,
                }
            )
            return ValueSpec(kind=ValueKind.ARRAY, element=self._scalar(inner))
        if dt == DataType.BINARY:
            return ValueSpec(
                kind=ValueKind.BINARY, length=(stats.avg_width if stats and stats.avg_width else 16)
            )
        if dt == DataType.ENUM and col.enum_values:
            return ValueSpec(kind=ValueKind.ENUM, values=col.enum_values)
        if col.nullable:
            self.notes.append(f"{col.name}: unsupported type '{col.native_type}', generating NULLs")
            return ValueSpec(kind=ValueKind.NULL)
        if col.default:
            self.notes.append(f"{col.name}: unsupported type '{col.native_type}', using column default")
            return ValueSpec(kind=ValueKind.CONSTANT, constant=None)
        self.notes.append(
            f"{col.name}: unsupported type '{col.native_type}', adapter will attempt a best-effort cast"
        )
        return ValueSpec(kind=ValueKind.TEXT, length=8)

    def _choice_from_common_values(self, col: Column) -> ValueSpec | None:
        """Weighted choice over explicitly sampled common values (opt-in only)."""
        stats = col.stats
        if not stats or not stats.common_values or not stats.top_frequencies:
            return None
        n = min(len(stats.common_values), len(stats.top_frequencies))
        if n == 0:
            return None
        values = stats.common_values[:n]
        weights = [max(0.0, float(w)) for w in stats.top_frequencies[:n]]
        if sum(weights) <= 0:
            return None
        dt = col.data_type
        if dt.is_textual:
            fallback = ValueKind.TEXT
        elif dt.is_integer:
            fallback = ValueKind.INTEGER
        elif dt == DataType.ENUM:
            fallback = ValueKind.ENUM
        else:
            return None
        spec = self._scalar_without_choice(col)
        return ValueSpec(
            kind=ValueKind.CHOICE,
            values=values,
            weights=weights,
            fallback=fallback,
            distinct=spec.distinct,
            min_value=spec.min_value,
            max_value=spec.max_value,
            length=spec.length,
        )

    def _scalar_without_choice(self, col: Column) -> ValueSpec:
        saved = col.stats.common_values if col.stats else None
        if col.stats:
            col.stats.common_values = None
        try:
            return self._scalar(col)
        finally:
            if col.stats:
                col.stats.common_values = saved

    def _is_ordered(self, col: Column) -> bool:
        if col.stats and col.stats.correlation is not None and abs(col.stats.correlation) > 0.5:
            return True
        return bool(_TEMPORAL_ORDERED_RE.search(col.name))

    def _null_fraction(self, table: Table, col: Column) -> float:
        if not col.nullable or col.name in table.primary_key:
            return 0.0
        if col.stats and col.stats.null_fraction is not None:
            return max(0.0, min(0.99, col.stats.null_fraction))
        if table.foreign_key_for(col.name):
            return 0.0
        return _DEFAULT_NULL_FRACTION


def _cap(value: int | None, cap: int) -> int | None:
    return None if value is None else min(value, cap)


def plan_generation(schema: Schema, seed: int = 42) -> GenerationPlan:
    return GenerationPlanner(schema, seed=seed).plan()
