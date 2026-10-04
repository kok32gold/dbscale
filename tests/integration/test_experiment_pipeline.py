"""End-to-end: inspect -> reproduce -> scale -> benchmark -> analyze -> recommend -> serialize."""

from __future__ import annotations

import pytest
from helpers_integration import read_query

from dbscale.core.config import load_config_dict
from dbscale.core.findings import FindingType, Severity
from dbscale.core.recommendations import RecommendationType
from dbscale.experiments import ExperimentRunner
from dbscale.reporting import load_result, write_result

pytestmark = pytest.mark.integration


class RecordingProgress:
    def __init__(self):
        self.events: list[tuple[str, str]] = []

    def start(self, m):
        self.events.append(("start", m))

    def done(self, m):
        self.events.append(("done", m))

    def fail(self, m):
        self.events.append(("fail", m))

    def note(self, m):
        self.events.append(("note", m))

    def progress(self, m):
        self.events.append(("progress", m))


def _config(source_url: str, sandbox_url: str, **overrides):
    data = {
        "name": "integration",
        "database": {"connection": source_url, "sample_common_values": True},
        "scale": {"targets": ["1x", "10x"], "seed": 3},
        "workload": {
            "runs": 3,
            "warmup": 1,
            "timeout_ms": 30_000,
            "queries": [
                {"name": "missing-index", "sql": read_query("missing_index")},
                {"name": "indexed-lookup", "sql": read_query("indexed_lookup")},
                {"name": "large-sort", "sql": read_query("large_sort")},
                {"name": "large-join", "sql": read_query("large_join")},
                {"name": "aggregation", "sql": read_query("aggregation")},
                {"name": "sku-lookup", "sql": read_query("high_cardinality_lookup")},
                {"name": "broken", "sql": "SELECT * FROM no_such_table"},
            ],
        },
        "thresholds": {"p95_ms": 50},
        "sandbox": {"type": "url", "url": sandbox_url},
        "ai": {"enabled": False},
    }
    data.update(overrides)
    return load_config_dict(data)


def test_full_pipeline_detects_known_bottlenecks(source_url, sandbox_url, tmp_path):
    cfg = _config(source_url, sandbox_url)
    progress = RecordingProgress()
    result = ExperimentRunner(cfg, progress).run()

    # --- progress narrative
    done = [m for kind, m in progress.events if kind == "done"]
    assert any(m.startswith("Connected to PostgreSQL") and "read-only" in m for m in done)
    assert any(m.startswith("Inspected schema: 6 tables") for m in done)
    assert any(m.startswith("Tested 1x") for m in done) and any(m.startswith("Tested 10x") for m in done)

    # --- result shape
    # one workload fails, the rest succeed: the run finishes and says so
    assert result.status == "partial" and result.finished_at is not None
    assert result.database.type == "postgres" and result.database.source_tables == 6
    assert [t.label for t in result.scale.targets] == ["1x", "10x"]
    assert result.scale.target("10x").rows["orders"] == pytest.approx(100_000, rel=0.05)
    assert len(result.workloads) == 7
    by_name = {w.query.name: w for w in result.workloads}
    for w in result.workloads:
        assert [m.scale_label for m in w.measurements] == ["1x", "10x"]

    # --- missing index: sequential scan with the filter column, add-index recommendation
    missing = by_name["missing-index"]
    assert all(m.ok and m.plan is not None for m in missing.measurements)
    seq = [f for f in missing.findings if f.type == FindingType.SEQUENTIAL_SCAN]
    assert seq and seq[0].table == "orders" and seq[0].columns == ["user_id"]
    assert seq[0].evidence["rows_scanned"] >= 100_000 and seq[0].evidence["rows_returned"] <= 100
    add_index = [r for r in missing.recommendations if r.type == RecommendationType.ADD_INDEX]
    assert add_index and add_index[0].table == "orders"
    assert add_index[0].columns[0] == "user_id"
    assert add_index[0].proposed_action.startswith('CREATE INDEX CONCURRENTLY "idx_orders_user_id')
    assert seq[0].id in add_index[0].finding_ids

    # --- indexed lookups stay healthy
    for name in ("indexed-lookup", "sku-lookup"):
        healthy = by_name[name]
        assert all(m.ok for m in healthy.measurements)
        assert all(m.rows_returned == 1 for m in healthy.measurements), name
        assert not [f for f in healthy.findings if f.type == FindingType.SEQUENTIAL_SCAN]
        assert healthy.scaling is not None and healthy.scaling.exponent is not None

    # --- large sort over events
    sort = by_name["large-sort"]
    assert [f for f in sort.findings if f.type == FindingType.LARGE_SORT]
    assert any(
        r.type == RecommendationType.ADD_INDEX and r.columns == ["occurred_at"] for r in sort.recommendations
    )

    # --- join query returns rows thanks to sampled common values + enum labels
    join = by_name["large-join"]
    assert all(m.ok and (m.rows_returned or 0) > 0 for m in join.measurements)

    # --- aggregation is at least measured correctly (1M-row thresholds are not reached at this scale)
    agg = by_name["aggregation"]
    assert agg.measurements[-1].plan.aggregates and agg.measurements[-1].plan.aggregates[0].group_keys == [
        "user_id"
    ]

    # --- a broken query fails gracefully and is reported, without aborting the run
    broken = by_name["broken"]
    assert all(m.error and "no_such_table" in m.error for m in broken.measurements)
    failure = [f for f in broken.findings if f.type == FindingType.EXECUTION_FAILURE]
    assert failure and failure[0].severity == Severity.HIGH

    # --- aggregate views and serialization
    assert result.findings and result.recommendations
    assert result.risk is not None
    path = write_result(result, tmp_path / "r.json")
    loaded = load_result(path)
    assert loaded.model_dump(by_alias=True) == result.model_dump(by_alias=True)
    assert loaded.workloads[0].measurements[0].raw_plan is not None


def test_external_sandbox_is_cleaned_up_unless_kept(source_url, sandbox_url):
    import psycopg

    cfg = _config(source_url, sandbox_url, scale={"targets": ["1x"]})
    cfg.workload.queries = cfg.workload.queries[:1]
    ExperimentRunner(cfg).run()
    with psycopg.connect(sandbox_url) as conn:
        rows = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'").fetchone()
    assert rows[0] == 0

    cfg.sandbox.keep = True
    ExperimentRunner(cfg).run()
    with psycopg.connect(sandbox_url) as conn:
        rows = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'").fetchone()
        assert rows[0] == 6
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == pytest.approx(10_000, rel=0.05)
    # a second run on a kept sandbox replaces the previous tables
    ExperimentRunner(cfg).run()
    cfg.sandbox.keep = False
    ExperimentRunner(cfg).run()


def test_source_database_is_untouched(source_url, sandbox_url):
    import psycopg

    def snapshot():
        with psycopg.connect(source_url) as conn:
            tables = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'").fetchone()[0]
            orders = conn.execute("SELECT count(*), sum(id) FROM orders").fetchone()
            indexes = conn.execute("SELECT count(*) FROM pg_indexes WHERE schemaname = 'public'").fetchone()[
                0
            ]
        return tables, orders, indexes

    before = snapshot()
    cfg = _config(source_url, sandbox_url, scale={"targets": ["1x"]})
    cfg.workload.queries = cfg.workload.queries[:2]
    ExperimentRunner(cfg).run()
    assert snapshot() == before
