"""CLI contracts: exit codes, friendly config errors, reports, and secret-free output."""

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from dbscale.cli.main import app
from dbscale.core.experiment import ExperimentResult
from fakes import RecordingAdapter
from helpers import ecommerce_schema

runner = CliRunner()
PASSWORD = "s3cret-source"


def _write_config(directory: Path, **overrides) -> Path:
    data = {
        "name": "cli-experiment",
        "database": {
            "type": "recording",
            "connection": f"postgresql://app:{PASSWORD}@db.internal/shop",
        },
        "scale": {"targets": ["1x"], "seed": 1},
        "workload": {"runs": 1, "warmup": 0, "queries": [{"name": "recent", "sql": "SELECT 1"}]},
        "sandbox": {"type": "url", "url": f"postgresql://sandbox:{PASSWORD}-box@127.0.0.1/db"},
        "ai": {"enabled": False},
    }
    data.update(overrides)
    path = directory / "dbscale.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def setup_function():
    RecordingAdapter.calls.clear()
    RecordingAdapter.schema = ecommerce_schema()
    RecordingAdapter.fail_populate = False


def test_inspect_is_read_only_and_hides_the_password(tmp_path):
    path = _write_config(tmp_path)
    result = runner.invoke(app, ["inspect", "-c", str(path), "--verbose"])
    assert result.exit_code == 0, result.stderr + result.stdout
    assert "orders" in result.stdout
    assert "Scale targets" in result.stdout
    assert PASSWORD not in result.stdout + result.stderr
    assert not any(call[0] == "populate" for call in RecordingAdapter.calls)


def test_version_and_help():
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert "dbscale" in version.stdout
    help_ = runner.invoke(app, ["--help"])
    assert help_.exit_code == 0
    assert "run" in help_.stdout and "inspect" in help_.stdout


def test_init_refuses_to_overwrite_unless_forced(tmp_path):
    first = runner.invoke(app, ["init", str(tmp_path), "--name", "alpha"])
    assert first.exit_code == 0
    assert (tmp_path / "dbscale.yaml").exists()
    assert "SELECT count(*)" in (tmp_path / "queries" / "example.sql").read_text()
    again = runner.invoke(app, ["init", str(tmp_path)])
    assert again.exit_code == 1
    assert "already exists" in again.stderr + again.stdout
    forced = runner.invoke(app, ["init", str(tmp_path), "--force", "--name", "beta"])
    assert forced.exit_code == 0
    assert "name: beta" in (tmp_path / "dbscale.yaml").read_text()


def test_missing_and_malformed_config(tmp_path):
    missing = runner.invoke(app, ["run", "-c", str(tmp_path / "nope.yaml")])
    assert missing.exit_code == 2
    assert "not found" in missing.stderr + missing.stdout
    bad = tmp_path / "dbscale.yaml"
    bad.write_text("- not\n- a mapping\n")
    malformed = runner.invoke(app, ["run", "-c", str(bad)])
    assert malformed.exit_code == 2
    assert "Configuration error" in malformed.stderr + malformed.stdout


def test_missing_query_file_exits_as_configuration_error(tmp_path):
    path = _write_config(
        tmp_path,
        workload={"queries": [{"name": "q", "file": "missing.sql"}]},
    )
    result = runner.invoke(app, ["run", "-c", str(path), "--json", str(tmp_path / "out.json")])
    assert result.exit_code == 2
    assert "not found" in (result.stderr + result.stdout)
    assert not (tmp_path / "out.json").exists()


def test_run_writes_json_without_passwords_and_fail_on_respects_severity(tmp_path):
    path = _write_config(tmp_path)
    out = tmp_path / "results.json"
    ok = runner.invoke(app, ["run", "-c", str(path), "--json", str(out), "--fail-on", "critical"])
    assert ok.exit_code == 0, ok.stderr + ok.stdout
    payload = json.loads(out.read_text())
    blob = out.read_text() + ok.stdout + ok.stderr
    assert PASSWORD not in blob
    assert "s3cret" not in blob
    assert payload["status"] == "completed"
    assert any(f["type"] == "sequential_scan" for f in payload["findings"])

    blocked = runner.invoke(app, ["run", "-c", str(path), "--json", str(out), "--fail-on", "low"])
    assert blocked.exit_code == 3
    bad_flag = runner.invoke(app, ["run", "-c", str(path), "--fail-on", "catastrophic"])
    assert bad_flag.exit_code == 2


def test_report_missing_file_and_round_trip(tmp_path):
    missing = runner.invoke(app, ["report", str(tmp_path / "missing.json")])
    assert missing.exit_code == 2
    path = _write_config(tmp_path)
    out = tmp_path / "results.json"
    assert runner.invoke(app, ["run", "-c", str(path), "--json", str(out)]).exit_code == 0
    rendered = runner.invoke(app, ["report", str(out), "--verbose"])
    assert rendered.exit_code == 0
    assert "cli-experiment" in rendered.stdout
    assert "Sequential scan" in rendered.stdout
    loaded = ExperimentResult.from_json(out.read_text())
    assert loaded.name == "cli-experiment"


def test_interrupted_run_exits_130(tmp_path, monkeypatch):
    path = _write_config(tmp_path)

    def boom(self):
        raise KeyboardInterrupt

    monkeypatch.setattr("dbscale.experiments.runner.ExperimentRunner.run", boom)
    result = runner.invoke(app, ["run", "-c", str(path)])
    assert result.exit_code == 130
    assert "Interrupted" in result.stderr + result.stdout


def test_explain_uses_injected_boundary_and_keeps_core_result(tmp_path, monkeypatch):
    path = _write_config(tmp_path)
    out = tmp_path / "results.json"
    assert runner.invoke(app, ["run", "-c", str(path), "--json", str(out)]).exit_code == 0

    def fake_ai(result, config, listener, provider=None):
        from dbscale.core.experiment import AIAnalysis

        return AIAnalysis(
            provider="fake", model="m", summary="uncertain: not enough evidence", recommendations=[]
        )

    monkeypatch.setattr("dbscale.experiments.run_ai_analysis", fake_ai)
    explained = runner.invoke(app, ["explain", str(out), "-c", str(path), "--no-save"])
    assert explained.exit_code == 0, explained.stderr + explained.stdout
    assert "uncertain" in explained.stdout
    saved = json.loads(out.read_text())
    assert saved.get("ai") is None  # --no-save leaves the file alone
