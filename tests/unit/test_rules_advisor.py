from dbscale.advisors.rules import RulesAdvisor
from dbscale.advisors.rules.advisor import create_index_sql, existing_index_prefix, index_name
from dbscale.analysis import AnalysisContext, analyze_workload
from dbscale.core.experiment import ThresholdSpec, WorkloadResult
from dbscale.core.findings import Finding, FindingType, Severity
from dbscale.core.measurements import NodeKind, PlanNode
from dbscale.core.recommendations import Impact, RecommendationType
from dbscale.core.schema import Index
from dbscale.core.workload import QuerySpec
from helpers import load_plan, measurement


def _analyzed(schema, name, *measurements, **thresholds):
    r = WorkloadResult(query=QuerySpec(name=name, sql="SELECT 1"), measurements=list(measurements))
    r.findings, r.scaling = analyze_workload(
        AnalysisContext(schema=schema, result=r, thresholds=ThresholdSpec(**thresholds))
    )
    r.recommendations = RulesAdvisor(schema).recommend(r)
    return r


def test_index_helpers(schema):
    assert index_name("orders", ["user_id", "created_at"]) == "idx_orders_user_id_created_at"
    assert len(index_name("t" * 60, ["a", "b"])) == 63
    assert create_index_sql(schema.table("orders"), ["user_id"]) == (
        'CREATE INDEX CONCURRENTLY "idx_orders_user_id" ON "public"."orders" ("user_id");'
    )
    assert existing_index_prefix(schema.table("order_items"), ["product_id"]) == "idx_order_items_product"
    assert existing_index_prefix(schema.table("orders"), ["user_id"]) is None


def test_missing_index_becomes_composite_add_index(schema):
    plan = load_plan("seq_scan_filter_sort")
    r = _analyzed(
        schema,
        "recent",
        measurement("recent", "1x", 1, 3, plan=plan, rows_returned=4),
        measurement("recent", "100x", 100, 22, plan=plan, rows_returned=4),
    )
    assert len(r.recommendations) == 1
    rec = r.recommendations[0]
    assert rec.type == RecommendationType.ADD_INDEX
    assert rec.table == "orders"
    # filter column first, then the sort key so the index also serves ORDER BY ... LIMIT
    assert rec.columns == ["user_id", "created_at"]
    assert (
        rec.proposed_action
        == 'CREATE INDEX CONCURRENTLY "idx_orders_user_id_created_at" ON "public"."orders" ("user_id", "created_at");'
    )
    assert rec.expected_impact == Impact.SIGNIFICANT
    assert rec.confidence >= 0.8
    assert rec.id == "recent:R1" and rec.source == "rules"
    assert set(rec.finding_ids) >= {
        f.id for f in r.findings if f.type in (FindingType.SEQUENTIAL_SCAN, FindingType.LARGE_SORT)
    }
    assert rec.tradeoffs


def test_existing_unused_index_becomes_investigate(schema):
    schema.table("orders").indexes.append(Index(name="idx_orders_user", columns=["user_id"]))
    plan = load_plan("seq_scan_filter_sort")
    r = _analyzed(schema, "q", measurement("q", "100x", 100, 22, plan=plan, rows_returned=4))
    rec = r.recommendations[0]
    assert rec.type == RecommendationType.INVESTIGATE
    assert "idx_orders_user" in rec.title
    assert rec.evidence["existing_index"] == "idx_orders_user"


def test_aggregation_yields_single_pre_aggregate_recommendation(schema):
    plan = load_plan("aggregate_spill")
    r = _analyzed(
        schema,
        "agg",
        measurement("agg", "1x", 1, 5, plan=plan),
        measurement("agg", "100x", 100, 330, plan=plan),
        p95_ms=100,
    )
    types = [rec.type for rec in r.recommendations]
    assert types == [RecommendationType.PRE_AGGREGATE]
    rec = r.recommendations[0]
    assert "user_id" in rec.proposed_action
    # supporting findings (threshold, scaling, full scan, sort) are attached as evidence, not duplicated
    assert set(rec.finding_ids) == {f.id for f in r.findings}
    assert rec.severity == Severity.CRITICAL  # p95 breach >4x the limit escalates the action


def test_ordered_scan_yields_ordering_index(schema):
    sort = PlanNode(
        kind=NodeKind.SORT,
        node_type="Sort",
        actual_rows=100,
        sort_keys=["orders.created_at DESC"],
        sort_method="top-N heapsort",
        children=[
            PlanNode(kind=NodeKind.SEQ_SCAN, node_type="Seq Scan", relation="orders", actual_rows=2_000_000)
        ],
    )
    r = _analyzed(schema, "latest", measurement("latest", "100x", 100, 150, plan=sort, rows_returned=100))
    recs = r.recommendations
    assert [x.type for x in recs] == [RecommendationType.ADD_INDEX]
    assert recs[0].columns == ["created_at"] and recs[0].table == "orders"
    assert "ordering index" in recs[0].title.lower()


def test_nested_loop_join_recommends_index_on_inner_join_key(schema):
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
    r = _analyzed(schema, "join", measurement("join", "10x", 10, 800, plan=root, rows_returned=10_000))
    add = [x for x in r.recommendations if x.type == RecommendationType.ADD_INDEX]
    # both the join analyzer and the seq-scan analyzer point at orders(user_id): deduplicated into one
    assert len(add) == 1 and add[0].table == "orders" and add[0].columns == ["user_id"]


def test_scaling_without_bottleneck_falls_back_to_investigate(schema):
    r = _analyzed(schema, "q", measurement("q", "1x", 1, 10), measurement("q", "100x", 100, 5000))
    assert len(r.recommendations) == 1
    assert r.recommendations[0].type == RecommendationType.INVESTIGATE
    assert r.recommendations[0].severity == Severity.HIGH


def test_timeout_recommendation(schema):
    r = _analyzed(
        schema,
        "q",
        measurement("q", "1x", 1, 10),
        measurement("q", "10x", 10, 0, error="Query exceeded timeout of 10 ms"),
    )
    rec = r.recommendations[0]
    assert rec.type == RecommendationType.INVESTIGATE and rec.severity == Severity.CRITICAL
    assert "timeout" in rec.explanation.lower()


def test_low_severity_findings_without_action_produce_nothing(schema):
    r = WorkloadResult(query=QuerySpec(name="q", sql="SELECT 1"))
    r.findings = [
        Finding(
            id="q:F1",
            type=FindingType.GROWING_LATENCY,
            severity=Severity.LOW,
            title="t",
            description="d",
            query_name="q",
        )
    ]
    recs = RulesAdvisor(schema).recommend(r)
    assert len(recs) == 1 and recs[0].type == RecommendationType.INVESTIGATE  # growth always gets a pointer
    r.findings = [
        Finding(
            id="q:F1",
            type=FindingType.DISK_SPILL,
            severity=Severity.LOW,
            title="t",
            description="d",
            query_name="q",
        )
    ]
    assert RulesAdvisor(schema).recommend(r) == []
