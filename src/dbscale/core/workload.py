"""Workload model. The MVP workload type is SQL; the model leaves room for others."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class QuerySpec(BaseModel):
    """A named SQL statement. The name is the stable identity used to compare runs."""

    name: str
    sql: str | None = None
    file: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> QuerySpec:
        if not self.sql and not self.file:
            raise ValueError(f"Query '{self.name}' needs either 'sql' or 'file'")
        return self

    def resolve(self, base_dir: Path | None = None) -> QuerySpec:
        """Return a copy with ``sql`` loaded from ``file`` if necessary."""
        if self.sql:
            return self
        path = Path(self.file or "")
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        if not path.exists():
            raise FileNotFoundError(f"Query '{self.name}': file not found: {path}")
        return self.model_copy(update={"sql": path.read_text().strip()})


class Workload(BaseModel):
    kind: str = Field(default="sql", description="Workload type; only 'sql' exists today")
    queries: list[QuerySpec]
    runs: int = Field(default=5, ge=1, description="Measured executions per query per scale")
    warmup: int = Field(default=1, ge=0, description="Unmeasured executions before measuring")
    timeout_ms: int = Field(default=120_000, ge=100)

    def resolve(self, base_dir: Path | None = None) -> Workload:
        names = [q.name for q in self.queries]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate query names: {', '.join(sorted(dupes))}")
        return self.model_copy(update={"queries": [q.resolve(base_dir) for q in self.queries]})
