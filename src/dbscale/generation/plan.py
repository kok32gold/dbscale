"""Database-independent description of synthetic data."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ValueKind(str, Enum):
    KEY_REF = "key_ref"  # value derived from an integer key of some table's column (PK, unique, FK)
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    TEXT = "text"
    TIMESTAMP = "timestamp"
    DATE = "date"
    TIME = "time"
    INTERVAL = "interval"
    UUID = "uuid"
    JSON = "json"
    BINARY = "binary"
    ENUM = "enum"
    ARRAY = "array"  # one-element array of ``element``
    CHOICE = "choice"  # weighted pick from explicit values (opt-in sampled common values)
    CONSTANT = "constant"
    NULL = "null"


class Picker(str, Enum):
    """How the integer key behind a KEY_REF is chosen for row ``i``."""

    SEQUENCE = "sequence"  # k = i                       (primary / unique keys)
    UNIFORM = "uniform"  # k ~ U(1, rows[ref_table])   (foreign keys)
    SKEWED = "skewed"  # k biased toward low ids      (hot parents)
    SELF_REF = "self_ref"  # k < i, referencing the same table (trees)
    RADIX = "radix"  # k = digit of a mixed-radix decomposition (composite keys)


class ValueSpec(BaseModel):
    kind: ValueKind

    # KEY_REF
    ref_table: str | None = None
    ref_column: str | None = None
    picker: Picker = Picker.SEQUENCE
    skew: float = Field(default=1.0, description=">1 concentrates foreign keys on low parent ids")
    radix_position: int | None = None
    radix: list[str] | None = Field(
        default=None,
        description="Table names whose row counts form the mixed radix (resolved at populate time)",
    )
    radix_base: str = Field(
        default="sequence", description="'sequence' (i) or 'uniform' (random within ref_table)"
    )

    # scalar generators
    distinct: int | None = Field(default=None, description="Target number of distinct values")
    min_value: float | None = None
    max_value: float | None = None
    decimals: int | None = None
    length: int | None = Field(default=None, description="Target text/binary length")
    values: list[str] | None = Field(default=None, description="Enum labels or explicit choice values")
    weights: list[float] | None = Field(
        default=None, description="Probabilities aligned with values (CHOICE)"
    )
    fallback: ValueKind | None = Field(
        default=None, description="Generator for the long tail not covered by weights"
    )
    ordered: bool = Field(default=False, description="Temporal values increase with row number")
    span_days: int = 730
    p_true: float = 0.5
    constant: Any = None
    element: ValueSpec | None = Field(default=None, description="Element generator when kind is ARRAY")


class ColumnPlan(BaseModel):
    name: str
    spec: ValueSpec
    null_fraction: float = 0.0


class TablePlan(BaseModel):
    table: str
    columns: list[ColumnPlan]
    guaranteed_unique: list[list[str]] = Field(
        default_factory=list, description="Column sets the generator guarantees to be unique"
    )

    def column(self, name: str) -> ColumnPlan | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def is_unique_safe(self, columns: list[str]) -> bool:
        target = sorted(columns)
        return any(sorted(u) == target for u in self.guaranteed_unique)


class GenerationPlan(BaseModel):
    tables: list[TablePlan] = Field(description="Parents before children")
    seed: int = 42
    notes: list[str] = Field(default_factory=list, description="Human-readable caveats about approximations")

    def table(self, name: str) -> TablePlan | None:
        for t in self.tables:
            if t.table == name:
                return t
        return None
