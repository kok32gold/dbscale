"""Boundaries, invalid input, plan parsing, LLM contract, and serialization properties."""

import json

import pytest

from dbscale.adapters.postgres.plan import parse_explain_json
from dbscale.analysis import AnalysisContext, analyze_workload
from dbscale.analysis.scaling import compute_scaling, fit_exponent
from dbscale.core.config import ConfigError, load_config, load_config_dict
from dbscale.core.experiment import ThresholdSpec, WorkloadResult
from dbscale.core.findings import FindingType, Severity
from dbscale.core.measurements import NodeKind, PlanNode, PlanSummary, percentile
from dbscale.core.scale import ScaleError, ScaleTarget, parse_count, resolve_scale
from dbscale.core.schema import Schema
from dbscale.core.workload import QuerySpec
from dbscale.generation import plan_generation
from dbscale.generation.plan import Picker, ValueKind
from dbscale.infrastructure.sandbox import SandboxError, create_sandbox
from helpers import ecommerce_schema, measurement

MINIMAL = {
    "database": {"connection": "postgresql://x"},
    "workload": {"queries": [{"name": "q", "sql": "SELECT 1"}]},
}


def _ctx(schema, *measurements, **thresholds):
    result = WorkloadResult(query=QuerySpec(name="q", sql="SELECT 1"), measurements=list(measurements))
    return AnalysisContext(schema=schema, result=result, thresholds=ThresholdSpec(**thresholds))


def _seq(rows_scanned: int, rows_out: int, *, filter_: str | None):
    return PlanSummary.from_root(
        PlanNode(
            kind=NodeKind.SEQ_SCAN,
            node_type="Seq Scan",
            relation="orders",
            actual_rows=rows_out,
            rows_removed_by_filter=rows_scanned - rows_out,
            filter=filter_,
            loops=1,
        )
    )


def test_config_edges(tmp_path):
    (tmp_path / "empty.yaml").write_text("")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "empty.yaml")
    (tmp_path / "list.yaml").write_text("- a\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(tmp_path / "list.yaml")
    with pytest.raises(ConfigError):
        load_config_dict({**MINIMAL, "scale": {"base_rows": 0}})
    with pytest.raises(ConfigError):
        load_config_dict({**MINIMAL, "workload": {"queries": MINIMAL["workload"]["queries"], "runs": "many"}})
    with pytest.raises(ConfigError):
        load_config_dict(
            {**MINIMAL, "workload": {"queries": MINIMAL["workload"]["queries"], "timeout_ms": 1}}
        )
    # unknown keys are ignored; the known contract still loads
    cfg = load_config_dict({**MINIMAL, "not_a_field": True, "name": "  spaced  "})
    assert cfg.name == "  spaced  "
    assert cfg.database.sample_common_values is False
    loaded = load_config_dict({**MINIMAL, "scale": {"targets": ["1x", "1x"]}})
    with pytest.raises(ScaleError, match="Duplicate"):
        resolve_scale(loaded.scale.parsed_targets(), ecommerce_schema())


def test_whitespace_query_file_and_special_characters(tmp_path):
    (tmp_path / "q.sql").write_text("   \n\n")
    cfg = load_config_dict(
        {
            "name": "café",
            "database": {"connection": "postgresql://x"},
            "workload": {"queries": [{"name": "空白", "file": "q.sql"}]},
        },
        base_dir=tmp_path,
    )
    resolved = cfg.resolved_workload()
    assert resolved.queries[0].name == "空白"
    assert resolved.queries[0].sql == ""


def test_scale_rejects_negative_and_clamps_explicit_zero(schema):
    with pytest.raises(ScaleError):
        parse_count(-1)
    with pytest.raises(ScaleError):
        parse_count(-1.5)
    with pytest.raises(ScaleError):
        resolve_scale([ScaleTarget.parse("-1x")], schema)
    with pytest.raises(ScaleError, match="positive"):
        resolve_scale([ScaleTarget.parse(-3)], schema)
    plan = resolve_scale([ScaleTarget.parse({"users": 0})], schema)
    assert plan.targets[0].rows["users"] == 1
    fractional = resolve_scale([ScaleTarget.parse("1.5x")], schema)
    assert fractional.targets[0].rows["users"] == 3000  # 2000 * 1.5
    with pytest.raises(ScaleError, match="no tables"):
        resolve_scale([ScaleTarget.parse("1x")], Schema(database_type="postgres"))


def test_larger_factor_never_reduces_rows_and_plans_are_deterministic(schema):
    plan = resolve_scale(
        [ScaleTarget.parse(f"{n}x") for n in (1, 2, 10, 100)],
        schema,
    )
    previous = None
    for target in plan.targets:
        if previous is not None:
            for name, rows in target.rows.items():
                assert rows >= previous.rows[name]
        previous = target
    first = plan_generation(schema, seed=99).model_dump()
    second = plan_generation(schema, seed=99).model_dump()
    assert first == second
    assert plan_generation(schema, seed=100).model_dump() != first


def test_foreign_keys_point_at_planned_parents(schema):
    plan = plan_generation(schema, seed=1)
    names = {t.table for t in plan.tables}
    for table in plan.tables:
        for column in table.columns:
            spec = column.spec
            if spec.kind == ValueKind.KEY_REF and spec.picker in (
                Picker.UNIFORM,
                Picker.SKEWED,
                Picker.SELF_REF,
            ):
                assert spec.ref_table in names


def test_sequential_scan_boundary(schema):
    below = analyze_workload(
        _ctx(schema, measurement("q", "1x", 1, 20, plan=_seq(99_999, 10, filter_="(user_id = 1)")))
    )
    assert FindingType.SEQUENTIAL_SCAN not in {f.type for f in below[0]}
    at = analyze_workload(
        _ctx(schema, measurement("q", "1x", 1, 20, plan=_seq(100_000, 50_000, filter_="(user_id = 1)")))
    )
    seq = [f for f in at[0] if f.type == FindingType.SEQUENTIAL_SCAN]
    assert seq and seq[0].severity == Severity.LOW
    selective = analyze_workload(
        _ctx(schema, measurement("q", "1x", 1, 20, plan=_seq(100_000, 10, filter_="(user_id = 1)")))
    )
    bumped = [f for f in selective[0] if f.type == FindingType.SEQUENTIAL_SCAN]
    assert bumped and bumped[0].severity == Severity.MEDIUM
    unfiltered = analyze_workload(
        _ctx(schema, measurement("q", "1x", 1, 20, plan=_seq(100_000, 100_000, filter_=None)))
    )
    plain = [f for f in unfiltered[0] if f.type == FindingType.SEQUENTIAL_SCAN]
    assert plain and plain[0].severity == Severity.INFO


def test_excessive_scan_is_dropped_when_the_seq_scan_already_explains_it(schema):
    plan = _seq(200_000, 10, filter_="(user_id = 1)")
    findings, _ = analyze_workload(_ctx(schema, measurement("q", "1x", 1, 30, plan=plan, rows_returned=10)))
    types = [f.type for f in findings]
    assert FindingType.SEQUENTIAL_SCAN in types
    assert FindingType.EXCESSIVE_ROWS_SCANNED not in types


def test_scaling_patterns(schema):
    def series(points):
        measurements = [
            measurement("q", f"{factor:g}x", factor, p50, plan=_seq(1000, 10, filter_=None))
            for factor, p50 in points
        ]
        return analyze_workload(_ctx(schema, *measurements))

    healthy, healthy_summary = series([(1, 20), (10, 22), (100, 25)])
    assert FindingType.NON_LINEAR_SCALING not in {f.type for f in healthy}
    assert FindingType.GROWING_LATENCY not in {f.type for f in healthy}
    assert healthy_summary.exponent is not None and healthy_summary.exponent < 0.6

    sudden, _ = series([(1, 20), (10, 25), (100, 5000)])
    nonlinear = [f for f in sudden if f.type == FindingType.NON_LINEAR_SCALING]
    assert nonlinear and nonlinear[0].severity == Severity.HIGH
    assert nonlinear[0].evidence["exponent"] > 1.15

    one, one_summary = series([(1, 20)])
    assert fit_exponent([(1, 20)]) is None
    assert one_summary.exponent is None
    assert FindingType.NON_LINEAR_SCALING not in {f.type for f in one}

    tiny, _ = series([(1, 1), (10, 2), (100, 40)])
    assert FindingType.NON_LINEAR_SCALING not in {f.type for f in tiny}


def test_threshold_boundaries(schema):
    plan = _seq(10, 10, filter_=None)
    # samples for p50=100 are 90,100,100,110,130; interpolated p95 is 126
    under = analyze_workload(_ctx(schema, measurement("q", "1x", 1, 100, plan=plan), p95_ms=126))
    assert FindingType.THRESHOLD_BREACH not in {f.type for f in under[0]}
    over = analyze_workload(_ctx(schema, measurement("q", "1x", 1, 100, plan=plan), p95_ms=125))
    breach = [f for f in over[0] if f.type == FindingType.THRESHOLD_BREACH]
    assert breach and breach[0].severity == Severity.MEDIUM
    assert breach[0].evidence["worst_ratio"] < 2


def test_insufficient_evidence_does_not_invent_a_scaling_exponent():
    result = WorkloadResult(query=QuerySpec(name="q", sql="SELECT 1"), measurements=[])
    summary = compute_scaling(result)
    assert summary.exponent is None
    assert summary.projected_next_10x_ms is None


def test_malformed_and_unknown_plans():
    with pytest.raises(ValueError, match="Unexpected EXPLAIN"):
        parse_explain_json({})
    with pytest.raises(ValueError):
        parse_explain_json([{"Planning Time": 1}])
    with pytest.raises(ValueError):
        parse_explain_json("not json")
    root, _, _ = parse_explain_json([{"Plan": {"Node Type": "Custom Scan", "Actual Rows": 3, "Plans": []}}])
    assert root.kind == NodeKind.OTHER
    nested = {"Node Type": "Limit", "Actual Rows": 1, "Plans": []}
    for _ in range(30):
        nested = {"Node Type": "Gather", "Actual Rows": 1, "Plans": [nested]}
    root, _, _ = parse_explain_json({"Plan": nested})
    assert sum(1 for _ in root.walk()) == 31


def test_percentile_rejects_empty_samples():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_llm_contract_rejects_garbage_and_keeps_structure():
    from dbscale.advisors.llm.advisor import LLMAdvisor
    from dbscale.core.recommendations import RecommendationType

    empty = LLMAdvisor.parse_response("", provider="p", model="m")
    assert empty.error and empty.recommendations == []
    missing = LLMAdvisor.parse_response("{}", provider="p", model="m")
    assert missing.error is None and missing.summary == "" and missing.recommendations == []
    extra = LLMAdvisor.parse_response(
        json.dumps(
            {
                "summary": "ok",
                "unexpected": {"sql": "DROP TABLE users"},
                "recommendations": [
                    {"title": "", "proposed_action": "", "confidence": 5, "type": "ADD_INDEX"},
                    "not-an-object",
                ],
            }
        ),
        provider="p",
        model="m",
    )
    assert extra.error is None
    assert len(extra.recommendations) == 1
    assert extra.recommendations[0].confidence == 1.0
    assert extra.recommendations[0].type == RecommendationType.ADD_INDEX
    assert extra.recommendations[0].source == "llm"
    assert "DROP TABLE" not in extra.summary
    not_object = LLMAdvisor.parse_response("[1, 2, 3]", provider="p", model="m")
    assert not_object.error


def test_report_with_no_findings_does_not_invent_a_risk(schema):
    from datetime import UTC, datetime
    from io import StringIO

    from rich.console import Console

    from dbscale.core.experiment import AIAnalysis, DatabaseInfo, ExperimentResult
    from dbscale.reporting import render_report

    result = ExperimentResult(
        dbscale_version="0.1.0",
        name="quiet",
        started_at=datetime.now(UTC),
        database=DatabaseInfo(type="postgres", version="16", source_tables=1, source_rows=10),
        schema=schema,
        scale=resolve_scale([ScaleTarget.parse("1x")], schema),
        workloads=[WorkloadResult(query=QuerySpec(name="q", sql="SELECT 1"))],
        ai=AIAnalysis(provider="fake", model="m", summary="", error="model unavailable"),
    )
    buf = StringIO()
    render_report(result, Console(file=buf, force_terminal=False, no_color=True))
    text = buf.getvalue()
    assert "No performance risks found" in text
    assert "AI analysis unavailable" in text or "model unavailable" in text
    assert "CREATE INDEX" not in text


def test_result_round_trip_preserves_unicode(schema, tmp_path):
    from datetime import UTC, datetime

    from dbscale.core.experiment import DatabaseInfo, ExperimentResult
    from dbscale.reporting import load_result, write_result

    result = ExperimentResult(
        dbscale_version="0.1.0",
        name="café — スケール",
        started_at=datetime.now(UTC),
        database=DatabaseInfo(type="postgres"),
        schema=schema,
        scale=resolve_scale([ScaleTarget.parse("1x")], schema),
        workloads=[WorkloadResult(query=QuerySpec(name="空白", sql="SELECT 'héllo'"))],
    )
    path = write_result(result, tmp_path / "nested" / "out.json")
    loaded = load_result(path)
    assert loaded.name == result.name
    assert loaded.workloads[0].query.sql == "SELECT 'héllo'"
    assert loaded.format_version == result.format_version


def test_sandbox_type_url_without_url_is_not_a_parse_error():
    cfg = load_config_dict({**MINIMAL, "sandbox": {"type": "url"}})
    assert cfg.sandbox.url is None
    with pytest.raises(SandboxError, match="sandbox.url"):
        create_sandbox(cfg.sandbox)
