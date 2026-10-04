import pytest

from dbscale.core.measurements import LatencyStats, NodeKind, PlanNode, PlanSummary, percentile
from helpers import load_plan


def test_percentile_interpolates():
    s = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(s, 50) == 3.0
    assert percentile(s, 0) == 1.0
    assert percentile(s, 100) == 5.0
    assert percentile(s, 95) == pytest.approx(4.8)
    assert percentile([7.0], 99) == 7.0
    with pytest.raises(ValueError):
        percentile([], 50)


def test_latency_stats():
    stats = LatencyStats.from_samples([5, 1, 3, 2, 4])
    assert stats.runs == 5 and stats.min_ms == 1 and stats.max_ms == 5
    assert stats.mean_ms == 3 and stats.p50_ms == 3
    with pytest.raises(ValueError):
        LatencyStats.from_samples([])


def _scan(relation, rows, removed=0, loops=1, filt=None, kind=NodeKind.SEQ_SCAN):
    return PlanNode(
        kind=kind,
        node_type="Seq Scan",
        relation=relation,
        actual_rows=rows,
        rows_removed_by_filter=removed,
        loops=loops,
        filter=filt,
        actual_total_ms=10.0,
    )


def test_summary_accounts_for_loops_and_filters():
    root = PlanNode(
        kind=NodeKind.NESTED_LOOP,
        node_type="Nested Loop",
        actual_rows=100,
        children=[
            _scan("users", 100, 900, filt="(country = 'US')"),
            _scan("orders", 1, 9, loops=100),
        ],
    )
    s = PlanSummary.from_root(root)
    assert s.rows_returned == 100
    # users: 100 kept + 900 removed; orders: (1 + 9) * 100 loops
    assert s.rows_scanned == 1000 + 1000
    assert len(s.seq_scans) == 2
    assert s.joins[0].kind == NodeKind.NESTED_LOOP
    assert s.joins[0].inner_loops == 100
    assert s.joins[0].inner_kind == NodeKind.SEQ_SCAN
    assert s.selectivity == pytest.approx(100 / 2000)


def test_summary_unwraps_hash_node_for_hash_joins():
    hash_node = PlanNode(
        kind=NodeKind.HASH, node_type="Hash", actual_rows=50, hash_batches=4, children=[_scan("users", 50)]
    )
    root = PlanNode(
        kind=NodeKind.HASH_JOIN,
        node_type="Hash Join",
        actual_rows=50,
        join_condition="(o.user_id = u.id)",
        children=[_scan("orders", 5000), hash_node],
    )
    s = PlanSummary.from_root(root)
    j = s.joins[0]
    assert j.inner_relation == "users" and j.hash_batches == 4 and j.outer_rows == 5000


def test_missing_row_counts_are_not_reported_as_zero():
    root = PlanNode(
        kind=NodeKind.SEQ_SCAN,
        node_type="Seq Scan",
        relation="orders",
        actual_rows=None,
        index_name=None,
    )
    summary = PlanSummary.from_root(root)
    assert summary.scans == []
    assert summary.rows_scanned is None
    assert summary.selectivity is None


def test_parse_real_seq_scan_plan():
    s = load_plan("seq_scan_filter_sort")
    assert s.rows_scanned == 999_999
    assert s.rows_returned == 4
    scan = s.seq_scans[0]
    assert scan.relation == "orders" and "user_id" in (scan.filter or "")
    assert scan.loops == 3  # parallel workers
    assert len(s.sorts) == 1 and s.sorts[0].keys == ["created_at DESC"]
    assert s.planning_ms is not None and s.execution_ms is not None


def test_parse_real_index_scan_plan():
    s = load_plan("index_scan")
    assert s.rows_scanned == 1 and s.rows_returned == 1
    assert not s.seq_scans
    assert s.scans[0].kind == NodeKind.INDEX_SCAN and s.scans[0].index_name == "users_pkey"


def test_parse_real_aggregate_plan():
    s = load_plan("aggregate_spill")
    agg = s.aggregates[0]
    assert agg.input_rows == 1_000_000
    assert agg.group_keys == ["user_id"]
    assert agg.disk_kb and agg.disk_kb > 0
    assert s.sorts[0].rows > 100_000


def test_parse_real_join_plan():
    s = load_plan("hash_join")
    assert s.joins and s.joins[0].kind == NodeKind.NESTED_LOOP
    kinds = {sc.kind for sc in s.scans}
    assert NodeKind.SEQ_SCAN in kinds and NodeKind.INDEX_SCAN in kinds
