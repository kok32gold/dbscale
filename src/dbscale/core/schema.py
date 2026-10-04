"""Normalized, database-independent schema model.

Adapters translate their native catalog into this representation. Nothing here
knows about PostgreSQL; ``native_type`` is an opaque string the owning adapter
understands and uses to reproduce the column in the sandbox.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DataType(str, Enum):
    """Generic type categories. Generators branch on these, never on native types."""

    SMALLINT = "smallint"
    INTEGER = "integer"
    BIGINT = "bigint"
    DECIMAL = "decimal"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TEXT = "text"
    VARCHAR = "varchar"
    CHAR = "char"
    UUID = "uuid"
    TIMESTAMP = "timestamp"
    TIMESTAMPTZ = "timestamptz"
    DATE = "date"
    TIME = "time"
    INTERVAL = "interval"
    JSON = "json"
    BINARY = "binary"
    ENUM = "enum"
    ARRAY = "array"
    NETWORK = "network"
    OTHER = "other"

    @property
    def is_integer(self) -> bool:
        return self in (DataType.SMALLINT, DataType.INTEGER, DataType.BIGINT)

    @property
    def is_numeric(self) -> bool:
        return self.is_integer or self in (DataType.DECIMAL, DataType.FLOAT)

    @property
    def is_textual(self) -> bool:
        return self in (DataType.TEXT, DataType.VARCHAR, DataType.CHAR)

    @property
    def is_temporal(self) -> bool:
        return self in (DataType.TIMESTAMP, DataType.TIMESTAMPTZ, DataType.DATE, DataType.TIME)


# Scalars the generator can fill without seeing a native type name.
# ENUM is handled separately (it needs labels). NETWORK and OTHER are not.
SYNTHETIC_SCALARS: frozenset[DataType] = frozenset(
    {
        DataType.SMALLINT,
        DataType.INTEGER,
        DataType.BIGINT,
        DataType.DECIMAL,
        DataType.FLOAT,
        DataType.BOOLEAN,
        DataType.TEXT,
        DataType.VARCHAR,
        DataType.CHAR,
        DataType.UUID,
        DataType.TIMESTAMP,
        DataType.TIMESTAMPTZ,
        DataType.DATE,
        DataType.TIME,
        DataType.INTERVAL,
        DataType.JSON,
        DataType.BINARY,
    }
)


class ColumnStats(BaseModel):
    """Privacy-safe column statistics.

    Only *shapes* are captured (fractions, counts, widths, frequencies).
    No actual values from the source database are ever stored here.
    """

    null_fraction: float | None = None
    distinct_count: int | None = Field(default=None, description="Estimated number of distinct values")
    avg_width: int | None = Field(default=None, description="Average value width in bytes")
    top_frequencies: list[float] | None = Field(
        default=None, description="Frequencies of the most common values (values themselves omitted)"
    )
    correlation: float | None = Field(
        default=None, description="Physical vs logical ordering correlation, -1..1"
    )
    common_values: list[str] | None = Field(
        default=None,
        description=(
            "Most common values as text, aligned with top_frequencies. Only populated when the user "
            "explicitly opts in (database.sample_common_values) for low-cardinality columns."
        ),
    )

    @property
    def skew(self) -> float:
        """Fraction of rows covered by the most common values (0 = uniform, 1 = fully concentrated)."""
        if not self.top_frequencies:
            return 0.0
        return min(1.0, sum(self.top_frequencies))


class Column(BaseModel):
    name: str
    data_type: DataType
    native_type: str
    nullable: bool = True
    default: str | None = None
    max_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    enum_values: list[str] | None = None
    is_generated: bool = False
    stats: ColumnStats | None = None
    # Catalog identity. ``regtype_name`` is what ``to_regtype`` accepts (no typmod).
    # ``type_extension`` is set when the type (or an array's element type) belongs to an extension.
    regtype_name: str | None = None
    type_extension: str | None = None
    array_element: DataType | None = None
    # Set by sandbox compatibility. ``None`` means "not adapted yet; use native_type".
    sandbox_type: str | None = None
    degraded_reason: str | None = None

    @property
    def physical_type(self) -> str:
        """Type written into sandbox DDL and casts."""
        return self.sandbox_type or self.native_type

    def can_synthesize(self) -> bool:
        """True when the generator can produce a value of this type, not merely NULL or a default."""
        if self.data_type == DataType.ENUM:
            return bool(self.enum_values)
        if self.data_type == DataType.ARRAY:
            return self.array_element in SYNTHETIC_SCALARS
        return self.data_type in SYNTHETIC_SCALARS


class ForeignKey(BaseModel):
    name: str
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str]

    @property
    def is_composite(self) -> bool:
        return len(self.columns) > 1


class Index(BaseModel):
    name: str
    columns: list[str] = Field(
        default_factory=list, description="Plain column references, expressions excluded"
    )
    unique: bool = False
    primary: bool = False
    method: str | None = None
    predicate: str | None = None
    has_expressions: bool = False
    definition: str | None = Field(
        default=None, description="Native DDL, used by the owning adapter to recreate"
    )
    skipped_reason: str | None = Field(
        default=None, description="Set when the sandbox cannot recreate this index"
    )


class Table(BaseModel):
    name: str
    schema_name: str | None = None
    columns: list[Column]
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    indexes: list[Index] = Field(default_factory=list)
    unique_constraints: list[list[str]] = Field(default_factory=list)
    estimated_rows: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}" if self.schema_name else self.name

    def column(self, name: str) -> Column | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def foreign_key_for(self, column: str) -> ForeignKey | None:
        for fk in self.foreign_keys:
            if column in fk.columns:
                return fk
        return None

    def is_unique_column(self, column: str) -> bool:
        if self.primary_key == [column]:
            return True
        if [column] in self.unique_constraints:
            return True
        return any(ix.unique and ix.columns == [column] and not ix.has_expressions for ix in self.indexes)


class Schema(BaseModel):
    database_type: str
    database_version: str | None = None
    tables: list[Table] = Field(default_factory=list)

    def table(self, name: str) -> Table | None:
        """Look up a table by bare or qualified name."""
        for t in self.tables:
            if t.name == name or t.qualified_name == name:
                return t
        return None

    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    @property
    def total_rows(self) -> int:
        return sum(t.estimated_rows for t in self.tables)

    def topological_order(self) -> list[Table]:
        """Parents before children. Cycles and self-references are tolerated."""
        by_name = {t.name: t for t in self.tables}
        ordered: list[Table] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(table: Table) -> None:
            if table.name in visited:
                return
            if table.name in visiting:
                return  # cycle: break it
            visiting.add(table.name)
            for fk in table.foreign_keys:
                parent = by_name.get(fk.referenced_table)
                if parent is not None and parent.name != table.name:
                    visit(parent)
            visiting.discard(table.name)
            visited.add(table.name)
            ordered.append(table)

        for t in sorted(self.tables, key=lambda t: t.name):
            visit(t)
        return ordered
