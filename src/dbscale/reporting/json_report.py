"""Machine-readable results (stable JSON; see docs/experiments/results.md)."""

from __future__ import annotations

from pathlib import Path

from dbscale.core.experiment import ExperimentResult


def write_result(result: ExperimentResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.to_json(indent=2))
    return path


def load_result(path: str | Path) -> ExperimentResult:
    return ExperimentResult.from_json(Path(path).read_text())
