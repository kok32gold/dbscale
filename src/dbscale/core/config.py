"""Declarative experiment configuration (``dbscale.yaml``).

The configuration is the user-facing contract. It deliberately exposes product
concepts (database, scale, workload, thresholds, sandbox, ai) and not internal ones.
``${VAR}`` and ``${VAR:-default}`` references are expanded from the environment.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from dbscale.core.experiment import ThresholdSpec
from dbscale.core.scale import ScaleError, ScaleTarget
from dbscale.core.workload import Workload

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    pass


class DatabaseConfig(BaseModel):
    type: str = "postgres"
    connection: str = Field(description="Connection URL of the *source* database (inspected read-only)")
    schemas: list[str] = Field(default_factory=lambda: ["public"])
    include_tables: list[str] | None = None
    exclude_tables: list[str] = Field(default_factory=list)
    sample_common_values: bool = Field(
        default=False,
        description=(
            "Opt-in: read the most common values of low-cardinality columns (e.g. status codes, country "
            "codes) so literal predicates in your queries match synthetic data. This is the only setting "
            "that copies actual values from the source database."
        ),
    )


class ScaleConfig(BaseModel):
    targets: list[Any] = Field(default_factory=lambda: ["1x", "10x", "100x"])
    base_rows: int = Field(
        default=1000, ge=1, description="Row count assumed for tables that are empty in the source"
    )
    seed: int = Field(default=42, description="Seed for deterministic synthetic data")

    def parsed_targets(self) -> list[ScaleTarget]:
        if not self.targets:
            raise ConfigError("scale.targets must contain at least one target")
        return [ScaleTarget.parse(t) for t in self.targets]


class SandboxConfig(BaseModel):
    type: str = Field(
        default="docker", description="'docker' (disposable container) or 'url' (bring your own)"
    )
    image: str = "postgres:16-alpine"
    url: str | None = Field(default=None, description="Connection URL when type is 'url'")
    keep: bool = Field(default=False, description="Keep the sandbox alive after the run")
    name: str | None = Field(default=None, description="Container name (docker)")
    unlogged_tables: bool = Field(
        default=True, description="Create sandbox tables UNLOGGED for faster loading"
    )
    foreign_keys: bool = Field(default=True, description="Reproduce foreign key constraints in the sandbox")

    @field_validator("type")
    @classmethod
    def _type_ok(cls, v: str) -> str:
        if v not in ("docker", "url"):
            raise ValueError("sandbox.type must be 'docker' or 'url'")
        return v


class AIConfig(BaseModel):
    enabled: bool = False
    provider: str = Field(
        default="openai", description="openai | anthropic | openai-compatible (ollama, etc.)"
    )
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    base_url: str | None = None
    timeout_s: float = 120.0
    max_plan_nodes: int = Field(
        default=40, description="Cap on plan nodes sent per query to limit prompt size"
    )


class OutputConfig(BaseModel):
    json_path: str | None = Field(
        default="dbscale-results.json", alias="json", description="Path for machine-readable results"
    )
    include_raw_plans: bool = True

    model_config = {"populate_by_name": True}


class ExperimentConfig(BaseModel):
    name: str = "dbscale-experiment"
    database: DatabaseConfig
    scale: ScaleConfig = Field(default_factory=ScaleConfig)
    workload: Workload
    thresholds: ThresholdSpec = Field(default_factory=ThresholdSpec)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    base_dir: Path | None = Field(default=None, exclude=True)

    def resolved_workload(self) -> Workload:
        return self.workload.resolve(self.base_dir)


def expand_env(value: Any, env: Mapping[str, str] | None = None) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in strings."""
    source: Mapping[str, str] = os.environ if env is None else env
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in source:
                return source[name]
            if default is not None:
                return default
            raise ConfigError(f"Environment variable '{name}' is not set (referenced as ${{{name}}})")

        return _ENV_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_env(v, source) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v, source) for v in value]
    return value


def load_config_dict(data: dict[str, Any], base_dir: Path | None = None) -> ExperimentConfig:
    try:
        cfg = ExperimentConfig.model_validate(expand_env(data))
    except ConfigError:
        raise
    except Exception as exc:  # pydantic ValidationError and friends
        raise ConfigError(f"Invalid configuration: {exc}") from exc
    cfg.base_dir = base_dir
    try:
        cfg.scale.parsed_targets()  # validate eagerly
    except ScaleError as exc:
        raise ConfigError(f"Invalid scale target: {exc}") from exc
    return cfg


def load_config(path: str | Path) -> ExperimentConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError("Configuration root must be a mapping")
    return load_config_dict(data, base_dir=path.parent.resolve())


DEFAULT_CONFIG_TEMPLATE = """\
# DBScale experiment configuration
# Docs: https://github.com/dbscale/dbscale
name: {name}

database:
  type: postgres
  # The source database is inspected read-only and never modified.
  connection: ${{DATABASE_URL}}
  schemas: [public]

scale:
  # How big should the synthetic database get? Factors or explicit row counts.
  targets:
    - 1x
    - 10x
    - 100x
  # Example of per-table targets:
  #  - users: 10M
  #    orders: 100M

workload:
  runs: 5
  warmup: 1
  queries:
    - name: example
      file: queries/example.sql

thresholds:
  p95_ms: 500

sandbox:
  type: docker
  image: postgres:16-alpine

ai:
  enabled: false
  provider: openai
  model: gpt-4o-mini
  api_key: ${{OPENAI_API_KEY:-}}

output:
  json: dbscale-results.json
"""
