import pytest
import yaml

from dbscale.core.config import ConfigError, expand_env, load_config, load_config_dict

MINIMAL = {
    "database": {"connection": "postgresql://x"},
    "workload": {"queries": [{"name": "q", "sql": "SELECT 1"}]},
}


def test_expand_env():
    env = {"DATABASE_URL": "postgresql://u:p@h/db", "EMPTY": ""}
    assert expand_env("${DATABASE_URL}", env) == "postgresql://u:p@h/db"
    assert expand_env("${MISSING:-fallback}", env) == "fallback"
    assert expand_env("${EMPTY:-fallback}", env) == ""
    assert expand_env({"a": ["${DATABASE_URL}", 1, None]}, env) == {"a": ["postgresql://u:p@h/db", 1, None]}
    with pytest.raises(ConfigError, match="MISSING"):
        expand_env("${MISSING}", env)


def test_defaults():
    cfg = load_config_dict(MINIMAL)
    assert cfg.database.type == "postgres"
    assert cfg.database.sample_common_values is False
    assert cfg.scale.targets == ["1x", "10x", "100x"]
    assert [t.factor for t in cfg.scale.parsed_targets()] == [1, 10, 100]
    assert cfg.workload.runs == 5 and cfg.workload.warmup == 1
    assert cfg.sandbox.type == "docker"
    assert cfg.ai.enabled is False
    assert cfg.output.json_path == "dbscale-results.json"


def test_invalid_config_is_reported():
    with pytest.raises(ConfigError):
        load_config_dict({"database": {"connection": "x"}})  # no workload
    with pytest.raises(ConfigError):
        load_config_dict({**MINIMAL, "sandbox": {"type": "kubernetes"}})
    with pytest.raises(ConfigError):
        load_config_dict({**MINIMAL, "scale": {"targets": []}})
    with pytest.raises(ConfigError):
        load_config_dict({**MINIMAL, "scale": {"targets": ["huge"]}})


def test_query_requires_sql_or_file():
    with pytest.raises(ConfigError):
        load_config_dict({**MINIMAL, "workload": {"queries": [{"name": "q"}]}})


def test_load_yaml_and_resolve_files(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env")
    (tmp_path / "queries").mkdir()
    (tmp_path / "queries" / "a.sql").write_text("SELECT 1;\n")
    (tmp_path / "dbscale.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "t",
                "database": {"connection": "${DATABASE_URL}"},
                "scale": {"targets": ["1x", {"users": "1M"}]},
                "workload": {
                    "queries": [{"name": "a", "file": "queries/a.sql"}, {"name": "b", "sql": "SELECT 2"}]
                },
                "thresholds": {"p95_ms": 250},
                "output": {"json": "out.json"},
            }
        )
    )
    cfg = load_config(tmp_path / "dbscale.yaml")
    assert cfg.database.connection == "postgresql://env"
    assert cfg.thresholds.p95_ms == 250
    assert cfg.output.json_path == "out.json"
    wl = cfg.resolved_workload()
    assert wl.queries[0].sql == "SELECT 1;"
    assert wl.queries[1].sql == "SELECT 2"
    labels = [t.label for t in cfg.scale.parsed_targets()]
    assert labels == ["1x", "users=1M"]


def test_missing_query_file(tmp_path):
    (tmp_path / "dbscale.yaml").write_text(
        yaml.safe_dump(
            {"database": {"connection": "x"}, "workload": {"queries": [{"name": "a", "file": "nope.sql"}]}}
        )
    )
    cfg = load_config(tmp_path / "dbscale.yaml")
    with pytest.raises(FileNotFoundError):
        cfg.resolved_workload()


def test_duplicate_query_names():
    cfg = load_config_dict(
        {
            **MINIMAL,
            "workload": {"queries": [{"name": "q", "sql": "SELECT 1"}, {"name": "q", "sql": "SELECT 2"}]},
        }
    )
    with pytest.raises(ValueError, match="Duplicate"):
        cfg.resolved_workload()


def test_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/dbscale.yaml")
