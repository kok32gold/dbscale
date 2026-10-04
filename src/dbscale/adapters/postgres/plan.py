"""Translate ``EXPLAIN (ANALYZE, FORMAT JSON)`` output into the normalized plan tree."""

from __future__ import annotations

from typing import Any

from dbscale.core.measurements import NodeKind, PlanNode

_KIND_MAP: dict[str, NodeKind] = {
    "Seq Scan": NodeKind.SEQ_SCAN,
    "Index Scan": NodeKind.INDEX_SCAN,
    "Index Only Scan": NodeKind.INDEX_ONLY_SCAN,
    "Bitmap Heap Scan": NodeKind.BITMAP_HEAP_SCAN,
    "Bitmap Index Scan": NodeKind.BITMAP_INDEX_SCAN,
    "Tid Scan": NodeKind.OTHER_SCAN,
    "Subquery Scan": NodeKind.OTHER_SCAN,
    "CTE Scan": NodeKind.OTHER_SCAN,
    "Function Scan": NodeKind.OTHER_SCAN,
    "Values Scan": NodeKind.OTHER_SCAN,
    "WorkTable Scan": NodeKind.OTHER_SCAN,
    "Foreign Scan": NodeKind.OTHER_SCAN,
    "Sort": NodeKind.SORT,
    "Incremental Sort": NodeKind.SORT,
    "Hash Join": NodeKind.HASH_JOIN,
    "Nested Loop": NodeKind.NESTED_LOOP,
    "Merge Join": NodeKind.MERGE_JOIN,
    "Hash": NodeKind.HASH,
    "Aggregate": NodeKind.AGGREGATE,
    "HashAggregate": NodeKind.AGGREGATE,
    "GroupAggregate": NodeKind.AGGREGATE,
    "MixedAggregate": NodeKind.AGGREGATE,
    "Group": NodeKind.AGGREGATE,
    "WindowAgg": NodeKind.OTHER,
    "Limit": NodeKind.LIMIT,
    "Gather": NodeKind.GATHER,
    "Gather Merge": NodeKind.GATHER,
    "Materialize": NodeKind.MATERIALIZE,
    "Memoize": NodeKind.MATERIALIZE,
}


def parse_explain_json(payload: Any) -> tuple[PlanNode, float | None, float | None]:
    """Accepts the JSON value returned by EXPLAIN (a one-element list)."""
    if isinstance(payload, list):
        payload = payload[0]
    if not isinstance(payload, dict) or "Plan" not in payload:
        raise ValueError("Unexpected EXPLAIN output")
    root = _node(payload["Plan"])
    return root, payload.get("Planning Time"), payload.get("Execution Time")


def _node(raw: dict[str, Any]) -> PlanNode:
    node_type = raw.get("Node Type", "Unknown")
    kind = _KIND_MAP.get(node_type, NodeKind.OTHER)
    removed = (raw.get("Rows Removed by Filter") or 0) + (raw.get("Rows Removed by Index Recheck") or 0)
    sort_keys = raw.get("Sort Key") or raw.get("Presorted Key")
    group_keys = raw.get("Group Key")
    peak = raw.get("Peak Memory Usage")
    disk = raw.get("Disk Usage")
    hash_batches = raw.get("Hash Batches") or raw.get("HashAgg Batches")
    workers = raw.get("Workers Launched", raw.get("Workers Planned"))
    strategy = raw.get("Strategy")
    if kind == NodeKind.AGGREGATE and strategy is None:
        strategy = {"HashAggregate": "Hashed", "GroupAggregate": "Sorted", "Group": "Sorted"}.get(node_type)

    extra = {
        k: v
        for k, v in raw.items()
        if k
        in (
            "Partial Mode",
            "Parallel Aware",
            "Scan Direction",
            "Recheck Cond",
            "Join Filter",
            "Rows Removed by Join Filter",
            "Output",
            "Heap Fetches",
            "Sort Space Type",
        )
    }
    return PlanNode(
        kind=kind,
        node_type=node_type,
        relation=raw.get("Relation Name"),
        alias=raw.get("Alias"),
        index_name=raw.get("Index Name"),
        estimated_rows=raw.get("Plan Rows"),
        actual_rows=raw.get("Actual Rows"),
        loops=int(raw.get("Actual Loops") or 1),
        actual_total_ms=raw.get("Actual Total Time"),
        startup_cost=raw.get("Startup Cost"),
        total_cost=raw.get("Total Cost"),
        rows_removed_by_filter=removed or None,
        filter=raw.get("Filter"),
        index_condition=raw.get("Index Cond") or raw.get("Recheck Cond"),
        join_condition=raw.get("Hash Cond") or raw.get("Merge Cond") or raw.get("Join Filter"),
        join_type=raw.get("Join Type"),
        sort_keys=list(sort_keys) if sort_keys else None,
        sort_method=raw.get("Sort Method"),
        sort_space_kb=raw.get("Sort Space Used"),
        sort_space_type=raw.get("Sort Space Type"),
        group_keys=list(group_keys) if group_keys else None,
        aggregate_strategy=strategy,
        hash_batches=hash_batches,
        peak_memory_kb=peak,
        disk_kb=disk,
        shared_hit_blocks=raw.get("Shared Hit Blocks"),
        shared_read_blocks=raw.get("Shared Read Blocks"),
        temp_written_blocks=raw.get("Temp Written Blocks"),
        workers=workers,
        children=[_node(c) for c in raw.get("Plans", [])],
        extra=extra,
    )
