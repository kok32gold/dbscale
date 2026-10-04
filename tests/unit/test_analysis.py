import pytest

from dbscale.analysis import AnalysisContext, analyze_workload, fit_exponent
from dbscale.analysis.analyzers import extract_columns
from dbscale.core.experiment import ThresholdSpec, WorkloadResult
from dbscale.core.findings import FindingType, Severity
from dbscale.core.measurements import NodeKind, PlanNode
from dbscale.core.workload import QuerySpec
from helpers import load_plan, measurement


def _result(name, *measurements):
    return WorkloadResult(query=QuerySpec(name=name, sql="SELECT 1"), measurements=list(measurements))


def _ctx(schema, result, **thresholds):
    return AnalysisContext(schema=schema, result=result, thresholds=ThresholdSpec(**thresholds))


def _types(findings):
    return {f.type for f in findings}


def test_fit_exponent():
    assert fit_exponent([(1, 10), (10, 100), (100, 1000)]) == pytest.approx(1.0)
    assert fit_exponent([(1, 10), (10, 10), (100, 10)]) == pytest.approx(0.0)
    assert fit_exponent([(1, 10)]) is None
    assert fit_exponent([(1, 1), (1, 2)]) is None  # same factor twice
    assert fit_exponent([(1, 10), (100, 10000)]) == pytest.approx(1.5)


def test_extract_columns(schema):
    orders = schema.table("orders")
    assert extract_columns("((status)::text = 'paid'::text) AND (user_id = 42)", orders) == [
        "status",
        "user_id",
    ]
    assert extract_columns("(o.created_at > (now() - '30 days'::interval))", orders, alias="o") == [
        "created_at"
    ]
    # the other side of a join condition is not a local column
    assert extract_columns("(o.user_id = u.id)", orders, alias="o") == ["user_id"]
    assert extract_columns("(user_id = u.id)", orders, alias="o") == ["user_id"]
    assert extract_columns("((status)::text = 'id'::text)", orders) == ["status"]  # literals are ignored
    assert extract_columns(None, orders) == []
    assert extract_columns("(x = 1)", None) == []


def test_sequential_scan_with_selective_filter_is_detected(schema):
    plan = load_plan("seq_scan_filter_sort")
    r = _result(
        "q",
        measurement("q", "1x", 1, 3, plan=plan, rows_returned=4),
        measurement("q", "100x", 100, 22, plan=plan, rows_returned=4),
    )
    findings, scaling = analyze_workload(_ctx(schema, r))
    seq = [f for f in findings if f.type == FindingType.SEQUENTIAL_SCAN]
    assert len(seq) == 1
    f = seq[0]
    assert f.table == "orders" and f.columns == ["user_id"]
    assert f.severity == Severity.MEDIUM  # 999,999 rows (low) bumped once for <0.1% selectivity
    assert f.evidence["rows_scanned"] == 999_999 and f.evidence["rows_returned"] == 4
    assert f.scale_label == "100x"
    assert f.id == "q:F1"
    # redundant "excessive rows" finding is folded into the sequential scan finding
    assert FindingType.EXCESSIVE_ROWS_SCANNED not in _types(findings)
    assert scaling.exponent is not None and scaling.last_label == "100x"


def test_index_scan_has_no_findings(schema):
    plan = load_plan("index_scan")
    r = _result(
        "q",
        measurement("q", "1x", 1, 3, plan=plan, rows_returned=1),
        measurement("q", "100x", 100, 4, plan=plan, rows_returned=1),
    )
    findings, scaling = analyze_workload(_ctx(schema, r, p95_ms=500))
    assert findings == []
    assert scaling.exponent is not None and scaling.exponent < 0.2


def test_threshold_breach_bumps_severity_and_adds_finding(schema):
    plan = load_plan("seq_scan_filter_sort")
    r = _result(
        "q", measurement("q", "1x", 1, 300, plan=plan), measurement("q", "100x", 100, 2500, plan=plan)
    )
    findings, _ = analyze_workload(_ctx(schema, r, p95_ms=500))
    types = _types(findings)
    assert FindingType.THRESHOLD_BREACH in types
    breach = next(f for f in findings if f.type == FindingType.THRESHOLD_BREACH)
    assert breach.severity == Severity.CRITICAL  # 2500*1.3 / 500 > 4x
    assert breach.evidence["breaches"][0]["scale"] == "100x"
    seq = next(f for f in findings if f.type == FindingType.SEQUENTIAL_SCAN)
    assert seq.severity == Severity.HIGH  # medium + threshold bump


def test_non_linear_scaling(schema):
    r = _result(
        "q",
        measurement("q", "1x", 1, 10),
        measurement("q", "10x", 10, 300),
        measurement("q", "100x", 100, 9000),
    )
    findings, scaling = analyze_workload(_ctx(schema, r))
    f = next(f for f in findings if f.type == FindingType.NON_LINEAR_SCALING)
    assert f.severity == Severity.HIGH
    assert f.evidence["exponent"] > 1.4
    assert scaling.projected_next_10x_ms > 9000 * 10


def test_growing_latency(schema):
    r = _result("q", measurement("q", "1x", 1, 10), measurement("q", "100x", 100, 600))
    findings, _ = analyze_workload(_ctx(schema, r))
    f = next(f for f in findings if f.type == FindingType.GROWING_LATENCY)
    assert f.severity == Severity.MEDIUM
    assert "p50_ms_by_scale" in f.evidence


def test_flat_or_tiny_latency_is_ignored(schema):
    r = _result("q", measurement("q", "1x", 1, 1), measurement("q", "100x", 100, 20))  # fast despite growth
    findings, _ = analyze_workload(_ctx(schema, r))
    assert not {FindingType.GROWING_LATENCY, FindingType.NON_LINEAR_SCALING} & _types(findings)


def test_large_sort_and_aggregation_spill(schema):
    plan = load_plan("aggregate_spill")
    r = _result("q", measurement("q", "100x", 100, 330, plan=plan))
    findings, _ = analyze_workload(_ctx(schema, r))
    types = _types(findings)
    assert FindingType.AGGREGATION_BOTTLENECK in types
    assert FindingType.LARGE_SORT in types
    agg = next(f for f in findings if f.type == FindingType.AGGREGATION_BOTTLENECK)
    assert agg.severity == Severity.HIGH and agg.evidence["disk_kb"] > 0
    assert agg.evidence["group_keys"] == ["user_id"]
    seq = next(f for f in findings if f.type == FindingType.SEQUENTIAL_SCAN)
    assert seq.columns == [] and seq.evidence["filter"] is None


def test_expensive_nested_loop_with_inner_seq_scan(schema):
    inner = PlanNode(
        kind=NodeKind.SEQ_SCAN,
        node_type="Seq Scan",
        relation="orders",
        alias="o",
        actual_rows=5,
        rows_removed_by_filter=99_995,
        loops=2000,
        filter="(user_id = u.id)",
    )
    outer = PlanNode(
        kind=NodeKind.SEQ_SCAN, node_type="Seq Scan", relation="users", alias="u", actual_rows=2000
    )
    root = PlanNode(
        kind=NodeKind.NESTED_LOOP,
        node_type="Nested Loop",
        actual_rows=10_000,
        join_condition="(o.user_id = u.id)",
        children=[outer, inner],
    )
    r = _result("q", measurement("q", "10x", 10, 800, plan=root, rows_returned=10_000))
    findings, _ = analyze_workload(_ctx(schema, r))
    join = next(f for f in findings if f.type == FindingType.EXPENSIVE_JOIN)
    assert join.severity == Severity.HIGH  # 200M inner rows read
    assert join.table == "orders" and join.columns == ["user_id"]
    assert join.evidence["inner_loops"] == 2000 and join.evidence["inner_rows_scanned"] == 200_000_000


def test_hash_join_batches(schema):
    hash_node = PlanNode(
        kind=NodeKind.HASH,
        node_type="Hash",
        actual_rows=2_000_000,
        hash_batches=8,
        children=[
            PlanNode(kind=NodeKind.SEQ_SCAN, node_type="Seq Scan", relation="orders", actual_rows=2_000_000)
        ],
    )
    root = PlanNode(
        kind=NodeKind.HASH_JOIN,
        node_type="Hash Join",
        actual_rows=100,
        join_condition="(o.user_id = u.id)",
        children=[
            PlanNode(kind=NodeKind.SEQ_SCAN, node_type="Seq Scan", relation="users", actual_rows=100),
            hash_node,
        ],
    )
    r = _result("q", measurement("q", "10x", 10, 900, plan=root, rows_returned=100))
    findings, _ = analyze_workload(_ctx(schema, r))
    join = next(f for f in findings if f.type == FindingType.EXPENSIVE_JOIN)
    assert join.evidence["hash_batches"] == 8 and join.severity == Severity.MEDIUM


def test_disk_sort_is_high(schema):
    sort = PlanNode(
        kind=NodeKind.SORT,
        node_type="Sort",
        actual_rows=50_000,
        sort_keys=["events.occurred_at DESC"],
        sort_method="external merge",
        sort_space_type="Disk",
        sort_space_kb=120_000,
        children=[
            PlanNode(kind=NodeKind.SEQ_SCAN, node_type="Seq Scan", relation="events", actual_rows=50_000)
        ],
    )
    r = _result("q", measurement("q", "1x", 1, 100, plan=sort, rows_returned=50_000))
    findings, _ = analyze_workload(_ctx(schema, r))
    f = next(f for f in findings if f.type == FindingType.LARGE_SORT)
    assert f.severity == Severity.HIGH and f.evidence["space_type"] == "Disk"


def test_execution_failure(schema):
    r = _result(
        "q",
        measurement("q", "1x", 1, 50),
        measurement("q", "10x", 10, 0, error="Query exceeded timeout of 1000 ms"),
    )
    findings, scaling = analyze_workload(_ctx(schema, r))
    f = next(f for f in findings if f.type == FindingType.EXECUTION_FAILURE)
    assert f.severity == Severity.CRITICAL and f.evidence["failed_scales"] == ["10x"]
    assert scaling.last_label == "1x"  # only successful measurements count


def test_findings_sorted_by_severity_with_stable_ids(schema):
    plan = load_plan("aggregate_spill")
    r = _result(
        "agg", measurement("agg", "1x", 1, 5, plan=plan), measurement("agg", "100x", 100, 330, plan=plan)
    )
    findings, _ = analyze_workload(_ctx(schema, r, p95_ms=100))
    ranks = [f.severity.rank for f in findings]
    assert ranks == sorted(ranks, reverse=True)
    assert [f.id for f in findings] == [f"agg:F{i}" for i in range(1, len(findings) + 1)]
