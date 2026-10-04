"""The database adapter contract.

Everything database-specific lives behind this interface: connecting,
inspecting the catalog, reproducing the schema in a sandbox, bulk-generating
synthetic rows, executing queries, and retrieving execution plans.

The core never branches on database type. It calls these methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from dbscale.core.measurements import PlanNode
from dbscale.core.scale import ResolvedScale
from dbscale.core.schema import Schema
from dbscale.generation.plan import GenerationPlan


class AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdapterCapabilities:
    """What an adapter can actually observe.

    ``False`` means the metric is unavailable. Callers must leave it unset.
    They must not substitute ``0`` or an empty plan for a metric the database
    cannot report.
    """

    execution_plans: bool = False
    rows_scanned: bool = False
    index_information: bool = False
    buffer_statistics: bool = False
    column_statistics: bool = False


@dataclass
class ServerInfo:
    type: str
    version: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    elapsed_ms: float
    rows_returned: int


@dataclass
class ExplainResult:
    root: PlanNode
    planning_ms: float | None
    execution_ms: float | None
    raw: Any


ProgressCallback = Callable[[str, int, int], None]
"""Called as ``callback(table_name, rows_done, rows_total)`` while populating."""


class DatabaseAdapter(ABC):
    """Contract every database integration implements.

    Two instances are typically alive during an experiment: one connected to
    the *source* (opened read-only, used only for ``server_info`` and
    ``inspect_schema``) and one connected to the *sandbox* (everything else).
    """

    type: ClassVar[str]
    capabilities: ClassVar[AdapterCapabilities] = AdapterCapabilities()

    # ------------------------------------------------------------ lifecycle

    @classmethod
    @abstractmethod
    def connect(cls, url: str, *, read_only: bool = False) -> DatabaseAdapter:
        """Open a connection. ``read_only=True`` must make writes impossible."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> DatabaseAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----------------------------------------------------------- discovery

    @abstractmethod
    def server_info(self) -> ServerInfo: ...

    @abstractmethod
    def inspect_schema(
        self,
        schemas: list[str] | None = None,
        include_tables: list[str] | None = None,
        exclude_tables: list[str] | None = None,
        *,
        sample_common_values: bool = False,
    ) -> Schema:
        """Read catalog + privacy-safe statistics into the normalized model. Must not write.

        ``sample_common_values`` is the only switch that reads actual values (the most common
        values of low-cardinality columns). It is off by default and must stay opt-in.
        """

    # -------------------------------------------------------- reproduction

    @abstractmethod
    def create_schema(self, schema: Schema, *, unlogged: bool = True) -> None:
        """Create tables (with primary keys) but without secondary indexes or foreign keys."""

    def adapt_to_sandbox(self, schema: Schema) -> list[str]:
        """Rewrite ``schema`` so this server can reproduce it.

        May substitute a column type, drop a generated expression, or mark an index
        skipped. Returns human-readable notes. The default implementation changes nothing.
        """
        return []

    def fidelity_notes(self) -> list[str]:
        """Notes recorded while adapting or creating the sandbox schema."""
        return []

    @abstractmethod
    def drop_schema(self, schema: Schema) -> None:
        """Remove everything ``create_schema`` created (used for external sandboxes)."""

    @abstractmethod
    def create_indexes(self, schema: Schema, plan: GenerationPlan) -> None:
        """Create secondary indexes. Uniqueness is only reproduced where the plan guarantees it."""

    @abstractmethod
    def drop_indexes(self, schema: Schema) -> None: ...

    @abstractmethod
    def create_foreign_keys(self, schema: Schema) -> None:
        """Add foreign keys without validating existing rows (generated data is referentially valid)."""

    @abstractmethod
    def drop_foreign_keys(self, schema: Schema) -> None:
        """Remove foreign keys so bulk loads are not slowed down by per-row checks."""

    @abstractmethod
    def truncate(self, schema: Schema) -> None: ...

    @abstractmethod
    def populate(
        self,
        schema: Schema,
        plan: GenerationPlan,
        scale: ResolvedScale,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Bulk-generate synthetic rows for every table according to the plan."""

    @abstractmethod
    def analyze(self, schema: Schema) -> None:
        """Refresh planner statistics after loading."""

    @abstractmethod
    def row_counts(self, schema: Schema) -> dict[str, int]: ...

    # ----------------------------------------------------------- execution

    @abstractmethod
    def execute(self, sql: str, *, timeout_ms: int) -> ExecutionResult:
        """Run the statement, consume all rows, return wall time and row count."""

    @abstractmethod
    def explain(self, sql: str, *, timeout_ms: int) -> ExplainResult:
        """Run the statement with instrumentation and return a normalized plan tree."""
