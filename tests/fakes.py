"""In-memory adapter and sandbox for orchestration tests. No Docker, no network."""

from __future__ import annotations

from dbscale.adapters.base import (
    AdapterCapabilities,
    AdapterError,
    DatabaseAdapter,
    ExecutionResult,
    ExplainResult,
    ServerInfo,
)
from dbscale.adapters.registry import register_adapter
from dbscale.core.measurements import NodeKind, PlanNode
from dbscale.core.scale import ResolvedScale
from dbscale.core.schema import Schema
from dbscale.generation.plan import GenerationPlan
from dbscale.infrastructure.sandbox import Sandbox

_WRITE_METHODS = {
    "create_schema",
    "drop_schema",
    "create_indexes",
    "drop_indexes",
    "create_foreign_keys",
    "drop_foreign_keys",
    "truncate",
    "populate",
    "analyze",
}


class RecordingAdapter(DatabaseAdapter):
    """Records every call. Writes on a read-only connection raise."""

    type = "recording"
    capabilities = AdapterCapabilities(
        execution_plans=True,
        rows_scanned=True,
        index_information=True,
        column_statistics=True,
    )
    calls: list[tuple] = []
    schema: Schema | None = None
    fail_populate: bool = False
    failing_sql: str = "FAIL_QUERY"

    def __init__(self, url: str, read_only: bool):
        self.url = url
        self.read_only = read_only
        self.closed = False

    @classmethod
    def connect(cls, url: str, *, read_only: bool = False) -> RecordingAdapter:
        inst = cls(url, read_only)
        cls.calls.append(("connect", read_only, url))
        return inst

    def close(self) -> None:
        self.closed = True
        type(self).calls.append(("close", self.read_only))

    def server_info(self) -> ServerInfo:
        type(self).calls.append(("server_info", self.read_only))
        return ServerInfo(type="recording", version="0")

    def inspect_schema(
        self, schemas=None, include_tables=None, exclude_tables=None, *, sample_common_values=False
    ):
        type(self).calls.append(("inspect_schema", self.read_only, sample_common_values))
        if self.read_only is False:
            raise AdapterError("inspect_schema must run on the source connection")
        return self.schema or Schema(database_type="recording")

    def _write(self, name: str) -> None:
        type(self).calls.append((name, self.read_only))
        if self.read_only:
            raise AdapterError("Refusing to write through a read-only (source) connection")

    def create_schema(self, schema: Schema, *, unlogged: bool = True) -> None:
        self._write("create_schema")

    def drop_schema(self, schema: Schema) -> None:
        self._write("drop_schema")

    def create_indexes(self, schema: Schema, plan: GenerationPlan) -> None:
        self._write("create_indexes")

    def drop_indexes(self, schema: Schema) -> None:
        self._write("drop_indexes")

    def create_foreign_keys(self, schema: Schema) -> None:
        self._write("create_foreign_keys")

    def drop_foreign_keys(self, schema: Schema) -> None:
        self._write("drop_foreign_keys")

    def truncate(self, schema: Schema) -> None:
        self._write("truncate")

    def populate(self, schema, plan, scale: ResolvedScale, progress=None) -> None:
        self._write("populate")
        if type(self).fail_populate:
            raise AdapterError("disk full while populating")
        if progress:
            for name, total in scale.rows.items():
                progress(name, total, total)

    def analyze(self, schema: Schema) -> None:
        self._write("analyze")

    def row_counts(self, schema: Schema) -> dict[str, int]:
        type(self).calls.append(("row_counts", self.read_only))
        return {t.name: t.estimated_rows for t in schema.tables}

    def execute(self, sql: str, *, timeout_ms: int) -> ExecutionResult:
        type(self).calls.append(("execute", self.read_only, sql, timeout_ms))
        if self.read_only:
            raise AdapterError("Refusing to execute workload SQL on the source connection")
        if self.failing_sql in (sql or ""):
            raise AdapterError("syntax error at FAIL_QUERY")
        if "TIMEOUT_QUERY" in (sql or ""):
            raise AdapterError(f"Query exceeded timeout of {timeout_ms} ms")
        return ExecutionResult(elapsed_ms=12.0, rows_returned=3)

    def explain(self, sql: str, *, timeout_ms: int) -> ExplainResult:
        type(self).calls.append(("explain", self.read_only, sql))
        if self.read_only:
            raise AdapterError("Refusing to explain on the source connection")
        if "EXPLAIN_FAIL" in (sql or ""):
            raise AdapterError("EXPLAIN failed")
        root = PlanNode(
            kind=NodeKind.SEQ_SCAN,
            node_type="Seq Scan",
            relation="orders",
            alias="orders",
            actual_rows=40,
            rows_removed_by_filter=199_960,
            filter="(user_id = 42)",
            actual_total_ms=18.0,
            loops=1,
        )
        return ExplainResult(
            root=root, planning_ms=0.4, execution_ms=18.0, raw={"Plan": {"Node Type": "Seq Scan"}}
        )


register_adapter(RecordingAdapter)


class MemorySandbox(Sandbox):
    def __init__(self, url: str = "postgresql://sandbox:unused@127.0.0.1/dbscale", *, fail: bool = False):
        self.url = url
        self.description = "memory"
        self.fail = fail
        self.started = 0
        self.destroyed = 0

    def start(self) -> str:
        self.started += 1
        if self.fail:
            raise RuntimeError("sandbox failed to start")
        return self.url

    def destroy(self) -> None:
        self.destroyed += 1
