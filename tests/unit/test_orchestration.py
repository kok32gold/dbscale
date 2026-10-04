"""Experiment runner against an in-memory adapter. Covers failure, recovery, and source safety."""

import pytest

from dbscale.adapters.base import AdapterError
from dbscale.advisors.llm import LLMProvider, LLMProviderError
from dbscale.core.config import ConfigError, load_config_dict
from dbscale.core.schema import Schema
from dbscale.experiments import ExperimentRunner
from dbscale.experiments.progress import NullProgress
from dbscale.experiments.runner import ExperimentError
from fakes import MemorySandbox, RecordingAdapter
from helpers import ecommerce_schema

PASSWORD = "s3cret-source"


class BoomProvider(LLMProvider):
    name = "boom"
    model = "m"

    def complete(self, system: str, user: str) -> str:
        raise LLMProviderError("provider down")


@pytest.fixture(autouse=True)
def _reset_adapter():
    RecordingAdapter.calls.clear()
    RecordingAdapter.schema = ecommerce_schema()
    RecordingAdapter.fail_populate = False
    RecordingAdapter.failing_sql = "FAIL_QUERY"
    yield
    RecordingAdapter.calls.clear()
    RecordingAdapter.fail_populate = False


def _config(**overrides):
    data = {
        "name": "orch",
        "database": {
            "type": "recording",
            "connection": f"postgresql://app:{PASSWORD}@db.internal/shop",
        },
        "scale": {"targets": ["1x"], "seed": 7, "base_rows": 100},
        "workload": {"runs": 1, "warmup": 0, "queries": [{"name": "recent", "sql": "SELECT 1"}]},
        "sandbox": {"type": "docker"},
        "ai": {"enabled": False},
        "output": {"json": None},
    }
    data.update(overrides)
    return load_config_dict(data)


def _run(cfg, sandbox=None, **kwargs):
    return ExperimentRunner(cfg, NullProgress(), sandbox=sandbox or MemorySandbox(), **kwargs).run()


def test_source_connection_is_read_only_and_sandbox_is_destroyed():
    sandbox = MemorySandbox(url=f"postgresql://sandbox:{PASSWORD}-box@127.0.0.1/db")
    result = _run(_config(), sandbox)
    source_ops = [c for c in RecordingAdapter.calls if c[1] is True]
    assert ("connect", True, f"postgresql://app:{PASSWORD}@db.internal/shop") in RecordingAdapter.calls
    assert {c[0] for c in source_ops} <= {"connect", "server_info", "inspect_schema", "close"}
    writes_on_source = [
        c
        for c in RecordingAdapter.calls
        if c[1] is True and c[0] in {"populate", "execute", "create_schema", "truncate"}
    ]
    assert writes_on_source == []
    assert sandbox.destroyed == 1
    assert result.status == "completed"
    blob = result.to_json()
    assert PASSWORD not in blob
    assert "db.internal" not in blob
    assert any(f.type.value == "sequential_scan" for f in result.findings)


def test_failed_query_is_partial_and_successful_query_is_kept():
    cfg = _config(
        workload={
            "runs": 1,
            "warmup": 0,
            "queries": [
                {"name": "ok", "sql": "SELECT 1"},
                {"name": "bad", "sql": "SELECT FAIL_QUERY"},
            ],
        }
    )
    result = _run(cfg)
    by_name = {w.query.name: w for w in result.workloads}
    assert by_name["ok"].measurements[0].ok
    assert "syntax error" in by_name["bad"].measurements[0].error
    assert result.status == "partial"
    assert any(f.query_name == "bad" and f.type.value == "execution_failure" for f in result.findings)


def test_every_query_failing_marks_the_experiment_failed_but_returns_results():
    cfg = _config(workload={"runs": 1, "warmup": 0, "queries": [{"name": "bad", "sql": "SELECT FAIL_QUERY"}]})
    result = _run(cfg)
    assert result.status == "failed"
    assert result.workloads[0].measurements[0].error


def test_later_scales_drop_foreign_keys_before_the_unique_indexes_they_depend_on():
    _run(_config(scale={"targets": ["1x", "10x"], "seed": 7, "base_rows": 100}))
    names = [c[0] for c in RecordingAdapter.calls]
    reload_at = names.index("truncate")
    assert names[reload_at - 2 : reload_at + 4] == [
        "drop_foreign_keys",
        "drop_indexes",
        "truncate",
        "populate",
        "create_indexes",
        "create_foreign_keys",
    ]


def test_populate_failure_destroys_sandbox_and_does_not_return_a_partial_result():
    RecordingAdapter.fail_populate = True
    sandbox = MemorySandbox()
    with pytest.raises(AdapterError, match="disk full"):
        _run(_config(), sandbox)
    assert sandbox.destroyed == 1
    assert not any(c[0] == "execute" for c in RecordingAdapter.calls)


def test_empty_schema_fails_before_the_sandbox_starts():
    RecordingAdapter.schema = Schema(database_type="recording")
    sandbox = MemorySandbox()
    with pytest.raises(ExperimentError, match="No tables"):
        _run(_config(), sandbox)
    assert sandbox.started == 0


def test_unknown_database_type_fails_before_the_sandbox_starts():
    sandbox = MemorySandbox()
    with pytest.raises(AdapterError, match="No adapter"):
        _run(_config(database={"type": "mongodb", "connection": "x"}), sandbox)
    assert sandbox.started == 0


def test_duplicate_query_names_are_configuration_errors():
    cfg = _config(
        workload={
            "queries": [{"name": "q", "sql": "SELECT 1"}, {"name": "q", "sql": "SELECT 2"}],
        }
    )
    with pytest.raises(ConfigError, match="Duplicate"):
        _run(cfg, MemorySandbox())


def test_same_inputs_produce_the_same_finding_ids():
    first = _run(_config())
    RecordingAdapter.calls.clear()
    second = _run(_config())
    assert [f.id for f in first.findings] == [f.id for f in second.findings]
    assert [r.id for r in first.recommendations] == [r.id for r in second.recommendations]


def test_ai_failure_does_not_discard_deterministic_results():
    result = _run(
        _config(ai={"enabled": True, "provider": "openai", "model": "x"}), llm_provider=BoomProvider()
    )
    assert result.status == "completed"
    assert result.findings
    assert result.ai is not None and result.ai.error == "provider down"
    assert result.ai.recommendations == []


def test_ai_disabled_does_not_call_a_provider():
    called = {"n": 0}

    class Spy(BoomProvider):
        def complete(self, system: str, user: str) -> str:
            called["n"] += 1
            return super().complete(system, user)

    result = _run(_config(ai={"enabled": False}), llm_provider=Spy())
    assert result.ai is None
    assert called["n"] == 0
