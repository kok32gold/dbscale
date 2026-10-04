"""Measurement model: latencies and normalized execution plans.

Adapters convert their native plan into a ``PlanNode`` tree with normalized
``NodeKind`` values. ``PlanSummary.from_root`` then derives every derived fact
(rows scanned, sequential scans, sorts, joins) database-independently.
"""

from __future__ import annotations

import statistics
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NodeKind(str, Enum):
    SEQ_SCAN = "seq_scan"
    INDEX_SCAN = "index_scan"
    INDEX_ONLY_SCAN = "index_only_scan"
    BITMAP_HEAP_SCAN = "bitmap_heap_scan"
    BITMAP_INDEX_SCAN = "bitmap_index_scan"
    OTHER_SCAN = "other_scan"
    SORT = "sort"
    HASH_JOIN = "hash_join"
    NESTED_LOOP = "nested_loop"
    MERGE_JOIN = "merge_join"
    HASH = "hash"
    AGGREGATE = "aggregate"
    LIMIT = "limit"
    GATHER = "gather"
    MATERIALIZE = "materialize"
    OTHER = "other"

    @property
    def is_table_scan(self) -> bool:
        return self in (
            NodeKind.SEQ_SCAN,
            NodeKind.INDEX_SCAN,
            NodeKind.INDEX_ONLY_SCAN,
            NodeKind.BITMAP_HEAP_SCAN,
        )

    @property
    def is_join(self) -> bool:
        return self in (NodeKind.HASH_JOIN, NodeKind.NESTED_LOOP, NodeKind.MERGE_JOIN)


class PlanNode(BaseModel):
    kind: NodeKind
    node_type: str = Field(description="Native node type name")
    relation: str | None = None
    alias: str | None = None
    index_name: str | None = None
    estimated_rows: float | None = None
    actual_rows: float | None = Field(default=None, description="Rows per loop as reported")
    loops: int = 1
    actual_total_ms: float | None = Field(default=None, description="Per-loop total time")
    startup_cost: float | None = None
    total_cost: float | None = None
    rows_removed_by_filter: float | None = None
    filter: str | None = None
    index_condition: str | None = None
    join_condition: str | None = None
    join_type: str | None = None
    sort_keys: list[str] | None = None
    sort_method: str | None = None
    sort_space_kb: int | None = None
    sort_space_type: str | None = None
    group_keys: list[str] | None = None
    aggregate_strategy: str | None = None
    hash_batches: int | None = None
    peak_memory_kb: int | None = None
    disk_kb: int | None = None
    shared_hit_blocks: int | None = None
    shared_read_blocks: int | None = None
    temp_written_blocks: int | None = None
    workers: int | None = None
    children: list[PlanNode] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def total_actual_rows(self) -> float:
        return (self.actual_rows or 0.0) * max(self.loops, 1)

    @property
    def total_rows_removed(self) -> float:
        return (self.rows_removed_by_filter or 0.0) * max(self.loops, 1)

    @property
    def total_time_ms(self) -> float:
        return (self.actual_total_ms or 0.0) * max(self.loops, 1)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class ScanInfo(BaseModel):
    kind: NodeKind
    relation: str
    alias: str | None = None
    index_name: str | None = None
    rows_scanned: int
    rows_output: int
    rows_removed_by_filter: int
    loops: int
    filter: str | None = None
    index_condition: str | None = None
    time_ms: float


class SortInfo(BaseModel):
    keys: list[str]
    rows: int
    method: str | None = None
    space_kb: int | None = None
    space_type: str | None = None
    time_ms: float


class JoinInfo(BaseModel):
    kind: NodeKind
    join_type: str | None = None
    condition: str | None = None
    rows: int
    outer_rows: int
    inner_rows: int = Field(description="Rows the inner side produced, summed over loops")
    inner_rows_scanned: int = Field(
        default=0, description="Rows the inner side read (incl. filtered), summed over loops"
    )
    inner_loops: int
    inner_kind: NodeKind | None = None
    inner_relation: str | None = None
    inner_alias: str | None = None
    hash_batches: int | None = None
    time_ms: float


class AggregateInfo(BaseModel):
    strategy: str | None = None
    group_keys: list[str] | None = None
    input_rows: int
    output_rows: int
    disk_kb: int | None = None
    time_ms: float


class PlanSummary(BaseModel):
    root: PlanNode
    planning_ms: float | None = None
    execution_ms: float | None = None
    rows_returned: int = 0
    rows_scanned: int | None = Field(
        default=None,
        description="Rows read by table scans. None when the adapter did not report them; 0 is a measured zero.",
    )
    scans: list[ScanInfo] = Field(default_factory=list)
    sorts: list[SortInfo] = Field(default_factory=list)
    joins: list[JoinInfo] = Field(default_factory=list)
    aggregates: list[AggregateInfo] = Field(default_factory=list)
    shared_hit_blocks: int | None = None
    shared_read_blocks: int | None = None
    temp_written_blocks: int | None = None

    @property
    def seq_scans(self) -> list[ScanInfo]:
        return [s for s in self.scans if s.kind == NodeKind.SEQ_SCAN]

    @property
    def index_scans(self) -> list[ScanInfo]:
        return [s for s in self.scans if s.kind != NodeKind.SEQ_SCAN]

    @property
    def selectivity(self) -> float | None:
        """rows_returned / rows_scanned. ``None`` if nothing was scanned."""
        if self.rows_scanned is None or self.rows_scanned <= 0:
            return None
        return self.rows_returned / self.rows_scanned

    @classmethod
    def from_root(
        cls, root: PlanNode, planning_ms: float | None = None, execution_ms: float | None = None
    ) -> PlanSummary:
        scans: list[ScanInfo] = []
        sorts: list[SortInfo] = []
        joins: list[JoinInfo] = []
        aggregates: list[AggregateInfo] = []
        saw_unobserved_scan = False
        for node in root.walk():
            if node.kind.is_table_scan and node.relation:
                if node.actual_rows is None:
                    # The node shape may be known, but a missing row count is not zero.
                    saw_unobserved_scan = True
                    continue
                removed = int(node.total_rows_removed)
                output = int(node.total_actual_rows)
                scans.append(
                    ScanInfo(
                        kind=node.kind,
                        relation=node.relation,
                        alias=node.alias,
                        index_name=node.index_name,
                        rows_scanned=output + removed,
                        rows_output=output,
                        rows_removed_by_filter=removed,
                        loops=node.loops,
                        filter=node.filter,
                        index_condition=node.index_condition,
                        time_ms=node.total_time_ms,
                    )
                )
            elif node.kind == NodeKind.SORT:
                sorts.append(
                    SortInfo(
                        keys=node.sort_keys or [],
                        rows=int(sum(c.total_actual_rows for c in node.children)),
                        method=node.sort_method,
                        space_kb=node.sort_space_kb,
                        space_type=node.sort_space_type,
                        time_ms=node.total_time_ms,
                    )
                )
            elif node.kind.is_join and len(node.children) >= 2:
                outer, inner = node.children[0], node.children[1]
                inner_eff = inner
                hash_batches = None
                if inner.kind == NodeKind.HASH and inner.children:
                    hash_batches = inner.hash_batches
                    inner_eff = inner.children[0]
                joins.append(
                    JoinInfo(
                        kind=node.kind,
                        join_type=node.join_type,
                        condition=node.join_condition,
                        rows=int(node.total_actual_rows),
                        outer_rows=int(outer.total_actual_rows),
                        inner_rows=int(inner_eff.total_actual_rows),
                        inner_rows_scanned=int(inner_eff.total_actual_rows + inner_eff.total_rows_removed),
                        inner_loops=inner_eff.loops,
                        inner_kind=inner_eff.kind,
                        inner_relation=inner_eff.relation,
                        inner_alias=inner_eff.alias,
                        hash_batches=hash_batches,
                        time_ms=node.total_time_ms,
                    )
                )
            elif node.kind == NodeKind.AGGREGATE:
                aggregates.append(
                    AggregateInfo(
                        strategy=node.aggregate_strategy,
                        group_keys=node.group_keys,
                        input_rows=int(sum(c.total_actual_rows for c in node.children)),
                        output_rows=int(node.total_actual_rows),
                        disk_kb=node.disk_kb,
                        time_ms=node.total_time_ms,
                    )
                )
        if scans:
            rows_scanned: int | None = int(sum(s.rows_scanned for s in scans))
        elif saw_unobserved_scan:
            rows_scanned = None
        else:
            rows_scanned = 0
        return cls(
            root=root,
            planning_ms=planning_ms,
            execution_ms=execution_ms,
            rows_returned=int(root.total_actual_rows) if root.actual_rows is not None else 0,
            rows_scanned=rows_scanned,
            scans=scans,
            sorts=sorts,
            joins=joins,
            aggregates=aggregates,
            shared_hit_blocks=root.shared_hit_blocks,
            shared_read_blocks=root.shared_read_blocks,
            temp_written_blocks=root.temp_written_blocks,
        )


class LatencyStats(BaseModel):
    runs: int
    min_ms: float
    max_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float

    @classmethod
    def from_samples(cls, samples: list[float]) -> LatencyStats:
        if not samples:
            raise ValueError("Need at least one latency sample")
        ordered = sorted(samples)
        return cls(
            runs=len(ordered),
            min_ms=ordered[0],
            max_ms=ordered[-1],
            mean_ms=statistics.fmean(ordered),
            p50_ms=percentile(ordered, 50),
            p95_ms=percentile(ordered, 95),
            p99_ms=percentile(ordered, 99),
        )


def percentile(sorted_samples: list[float], pct: float) -> float:
    """Nearest-rank percentile with linear interpolation on a pre-sorted list."""
    if not sorted_samples:
        raise ValueError("empty samples")
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    k = (len(sorted_samples) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_samples) - 1)
    frac = k - lo
    return sorted_samples[lo] + (sorted_samples[hi] - sorted_samples[lo]) * frac


class QueryMeasurement(BaseModel):
    """Everything measured for one query at one scale. Facts only; no conclusions."""

    query_name: str
    scale_label: str
    scale_factor: float
    total_rows: int = Field(description="Total synthetic rows in the sandbox at this scale")
    latency: LatencyStats | None = None
    samples_ms: list[float] = Field(default_factory=list)
    rows_returned: int | None = None
    plan: PlanSummary | None = None
    raw_plan: Any | None = Field(default=None, description="Native plan as returned by the database")
    error: str | None = Field(default=None, description="Set when the query itself failed or timed out")
    plan_error: str | None = Field(default=None, description="Set when timing succeeded but EXPLAIN failed")

    @property
    def ok(self) -> bool:
        return self.error is None and self.latency is not None
