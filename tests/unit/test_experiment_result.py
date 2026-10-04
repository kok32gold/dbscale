from datetime import UTC, datetime

from dbscale.core.experiment import (
    RESULT_FORMAT_VERSION,
    AIAnalysis,
    DatabaseInfo,
    ExperimentResult,
    WorkloadResult,
)
from dbscale.core.findings import Finding, FindingType, Severity
from dbscale.core.recommendations import Recommendation, RecommendationType
from dbscale.core.scale import ScaleTarget, resolve_scale
from dbscale.core.workload import QuerySpec
from dbscale.reporting import load_result, write_result
from helpers import load_plan, measurement


def _result(schema):
    plan = load_plan("seq_scan_filter_sort")
    finding = Finding(
        id="q:F1",
        type=FindingType.SEQUENTIAL_SCAN,
        severity=Severity.HIGH,
        title="t",
        description="d",
        query_name="q",
        table="orders",
        evidence={"rows_scanned": 999_999},
    )
    rec = Recommendation(
        id="q:R1",
        type=RecommendationType.ADD_INDEX,
        severity=Severity.HIGH,
        title="t",
        explanation="e",
        proposed_action="CREATE INDEX",
        confidence=0.8,
        finding_ids=["q:F1"],
    )
    w = WorkloadResult(
        query=QuerySpec(name="q", sql="SELECT 1"),
        measurements=[measurement("q", "1x", 1, 3, plan=plan)],
        findings=[finding],
        recommendations=[rec],
    )
    return ExperimentResult(
        dbscale_version="0.1.0",
        name="exp",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        database=DatabaseInfo(type="postgres", version="16.1", source_tables=5, source_rows=100),
        schema=schema,
        scale=resolve_scale([ScaleTarget.parse("1x")], schema),
        workloads=[w],
        findings=[finding],
        recommendations=[rec],
        ai=AIAnalysis(provider="fake", model="m", summary="s"),
    )


def test_json_round_trip(schema, tmp_path):
    result = _result(schema)
    path = write_result(result, tmp_path / "out" / "r.json")
    loaded = load_result(path)
    assert loaded.format_version == RESULT_FORMAT_VERSION
    assert loaded.name == "exp" and loaded.database.version == "16.1"
    assert loaded.schema_model.table("orders").column("user_id") is not None
    assert loaded.workloads[0].measurements[0].plan.rows_scanned == 999_999
    assert loaded.workloads[0].measurements[0].raw_plan is None  # fixture measurement carries no raw plan
    assert loaded.findings[0].evidence == {"rows_scanned": 999_999}
    assert loaded.recommendations[0].type == RecommendationType.ADD_INDEX
    assert loaded.ai.provider == "fake"
    assert loaded.risk == Severity.HIGH
    assert loaded.workloads[0].risk == Severity.HIGH
    assert loaded.model_dump(by_alias=True) == result.model_dump(by_alias=True)


def test_schema_key_is_named_schema_in_json(schema):
    text = _result(schema).to_json()
    assert '"schema":' in text and '"schema_":' not in text
