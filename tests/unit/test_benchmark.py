"""Benchmark orchestration: timing, partial failure, plan collection."""

from dbscale.adapters.base import AdapterCapabilities, AdapterError, ExecutionResult, ExplainResult
from dbscale.benchmark.runner import BenchmarkRunner, measure_query
from dbscale.core.measurements import NodeKind, PlanNode
from dbscale.core.scale import ResolvedScale
from dbscale.core.workload import QuerySpec, Workload


class Scripted:
    def __init__(self, *, explain_error: str | None = None):
        self.explain_error = explain_error
        self.executed: list[str] = []
        self.explained: list[str] = []

    def execute(self, sql: str, *, timeout_ms: int) -> ExecutionResult:
        self.executed.append(sql)
        if "BAD" in sql:
            raise AdapterError("syntax error near BAD")
        if "SLOW" in sql:
            raise AdapterError(f"Query exceeded timeout of {timeout_ms} ms")
        return ExecutionResult(elapsed_ms=10.0 + len(self.executed), rows_returned=4)

    def explain(self, sql: str, *, timeout_ms: int) -> ExplainResult:
        self.explained.append(sql)
        if self.explain_error:
            raise AdapterError(self.explain_error)
        root = PlanNode(kind=NodeKind.OTHER, node_type="Result", actual_rows=4)
        return ExplainResult(root=root, planning_ms=0.2, execution_ms=1.0, raw={"kept": True})


def _scale() -> ResolvedScale:
    return ResolvedScale(label="1x", factor=1.0, rows={"t": 10})


def test_warmup_is_not_counted_as_a_sample():
    adapter = Scripted()
    m = measure_query(
        adapter, QuerySpec(name="q", sql="SELECT 1"), _scale(), runs=3, warmup=2, timeout_ms=1000
    )
    assert m.ok
    assert len(adapter.executed) == 5
    assert m.latency is not None and m.latency.runs == 3
    assert len(m.samples_ms) == 3
    assert m.rows_returned == 4
    assert m.plan is not None and m.raw_plan == {"kept": True}


def test_adapter_without_plans_does_not_invent_one():
    adapter = Scripted()
    adapter.capabilities = AdapterCapabilities()
    measurement = measure_query(
        adapter, QuerySpec(name="q", sql="SELECT 1"), _scale(), runs=1, warmup=0, timeout_ms=1000
    )
    assert measurement.ok and measurement.plan is None and measurement.raw_plan is None
    assert adapter.explained == []
    assert measurement.plan_error is not None
    assert "execution_plans" in measurement.plan_error


def test_explain_failure_keeps_latency():
    adapter = Scripted(explain_error="plan collector crashed")
    m = measure_query(
        adapter, QuerySpec(name="q", sql="SELECT 1"), _scale(), runs=1, warmup=0, timeout_ms=1000
    )
    assert m.ok and m.plan is None
    assert m.plan_error == "plan collector crashed"
    assert m.latency is not None and m.latency.p50_ms > 0


def test_query_error_stops_that_query_only():
    adapter = Scripted()
    workload = Workload(
        queries=[
            QuerySpec(name="ok", sql="SELECT 1"),
            QuerySpec(name="bad", sql="SELECT BAD"),
            QuerySpec(name="slow", sql="SELECT SLOW"),
        ],
        runs=2,
        warmup=1,
        timeout_ms=500,
    )
    measurements = BenchmarkRunner(adapter, workload, include_raw_plans=False).run_scale(_scale())
    ok, bad, slow = measurements
    assert ok.ok and ok.raw_plan is None and ok.plan is not None
    assert bad.error and "syntax error" in bad.error and bad.latency is None
    assert slow.error and "timeout" in slow.error
    assert [m.query_name for m in measurements] == ["ok", "bad", "slow"]
    # failed queries are not explained; the successful one is
    assert adapter.explained == ["SELECT 1"]


def test_raw_plan_can_be_omitted():
    adapter = Scripted()
    m = measure_query(
        adapter,
        QuerySpec(name="q", sql="SELECT 1"),
        _scale(),
        runs=1,
        warmup=0,
        timeout_ms=1000,
        include_raw_plan=False,
    )
    assert m.plan is not None and m.raw_plan is None
