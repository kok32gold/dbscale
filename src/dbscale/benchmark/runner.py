"""Execute workloads against the sandbox and collect measurements."""

from __future__ import annotations

from collections.abc import Callable

from dbscale.adapters.base import AdapterError, DatabaseAdapter
from dbscale.core.measurements import LatencyStats, PlanSummary, QueryMeasurement
from dbscale.core.scale import ResolvedScale
from dbscale.core.workload import QuerySpec, Workload

QueryProgress = Callable[[QuerySpec, QueryMeasurement], None]


def measure_query(
    adapter: DatabaseAdapter,
    query: QuerySpec,
    scale: ResolvedScale,
    *,
    runs: int,
    warmup: int,
    timeout_ms: int,
    include_raw_plan: bool = True,
) -> QueryMeasurement:
    """Warm up, run ``runs`` timed executions, then collect one instrumented plan."""
    sql = query.sql or ""
    measurement = QueryMeasurement(
        query_name=query.name,
        scale_label=scale.label,
        scale_factor=scale.factor,
        total_rows=scale.total_rows,
    )
    try:
        for _ in range(warmup):
            adapter.execute(sql, timeout_ms=timeout_ms)
        samples: list[float] = []
        rows_returned = 0
        for _ in range(runs):
            result = adapter.execute(sql, timeout_ms=timeout_ms)
            samples.append(result.elapsed_ms)
            rows_returned = result.rows_returned
        measurement.samples_ms = samples
        measurement.latency = LatencyStats.from_samples(samples)
        measurement.rows_returned = rows_returned
    except AdapterError as exc:
        measurement.error = str(exc)
        return measurement

    if not _collects_plans(adapter):
        measurement.plan_error = (
            "Execution plans are not available for this adapter "
            "(capabilities.execution_plans is false). Latency was measured; "
            "plan-based findings are omitted."
        )
        return measurement

    try:
        explained = adapter.explain(sql, timeout_ms=timeout_ms)
        measurement.plan = PlanSummary.from_root(
            explained.root, explained.planning_ms, explained.execution_ms
        )
        if include_raw_plan:
            measurement.raw_plan = explained.raw
    except AdapterError as exc:
        # Timing succeeded; keep the latency facts and record that the plan is missing.
        measurement.plan_error = str(exc)
    return measurement


def _collects_plans(adapter: DatabaseAdapter) -> bool:
    """Duck-typed doubles without ``capabilities`` still collect plans."""
    caps = getattr(adapter, "capabilities", None)
    if caps is None:
        return True
    return bool(caps.execution_plans)


class BenchmarkRunner:
    def __init__(self, adapter: DatabaseAdapter, workload: Workload, *, include_raw_plans: bool = True):
        self.adapter = adapter
        self.workload = workload
        self.include_raw_plans = include_raw_plans

    def run_scale(
        self, scale: ResolvedScale, on_query: QueryProgress | None = None
    ) -> list[QueryMeasurement]:
        measurements: list[QueryMeasurement] = []
        for query in self.workload.queries:
            m = measure_query(
                self.adapter,
                query,
                scale,
                runs=self.workload.runs,
                warmup=self.workload.warmup,
                timeout_ms=self.workload.timeout_ms,
                include_raw_plan=self.include_raw_plans,
            )
            measurements.append(m)
            if on_query:
                on_query(query, m)
        return measurements
