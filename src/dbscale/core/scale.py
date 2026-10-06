"""Scale model: how large should each table become?

Users express targets as ``"10x"``, ``"1M users"``, ``{"users": "10M", "orders": 100000000}``,
or a bare number (interpreted as a factor). Targets are resolved against the
source schema's cardinality estimates into exact per-table row counts.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from dbscale.core.schema import Schema

_SUFFIXES = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "g": 1_000_000_000}
_NUMBER = r"\d+(?:_\d+)*(?:\.\d+)?"
_COUNT_RE = re.compile(rf"^\s*({_NUMBER})\s*([kKmMbBgG])?\s*$")
_FACTOR_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[xX]\s*$")
_COUNT_TABLE_RE = re.compile(rf"^\s*({_NUMBER}\s*[kKmMbBgG]?)\s+([A-Za-z_][\w.]*)\s*$")


class ScaleError(ValueError):
    pass


def parse_count(value: Any) -> int:
    """Parse ``1_000_000``, ``"1M"``, ``"2.5k"`` or an int into an integer row count."""
    if isinstance(value, bool):
        raise ScaleError(f"Invalid row count: {value!r}")
    if isinstance(value, int):
        count = value
    elif isinstance(value, float):
        count = int(value)
    elif isinstance(value, str):
        m = _COUNT_RE.match(value)
        if not m:
            raise ScaleError(f"Invalid row count: {value!r} (expected e.g. 1000000, 1M, 250k)")
        number = m.group(1).replace("_", "")
        mult = _SUFFIXES[m.group(2).lower()] if m.group(2) else 1
        count = int(float(number) * mult)
    else:
        raise ScaleError(f"Invalid row count: {value!r}")
    if count < 0:
        raise ScaleError(f"Row count must be non-negative: {value!r}")
    return count


def format_count(n: int) -> str:
    if n >= 1_000_000_000:
        return _trim(n / 1_000_000_000) + "B"
    if n >= 1_000_000:
        return _trim(n / 1_000_000) + "M"
    if n >= 1_000:
        return _trim(n / 1_000) + "K"
    return str(n)


def _trim(x: float) -> str:
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s


class ScaleTarget(BaseModel):
    """An unresolved scale target as written by the user."""

    factor: float | None = Field(default=None, description="Uniform multiplier applied to every table")
    rows: dict[str, int] = Field(default_factory=dict, description="Explicit per-table row counts")

    @classmethod
    def parse(cls, raw: Any) -> ScaleTarget:
        if isinstance(raw, ScaleTarget):
            return raw
        if isinstance(raw, bool):
            raise ScaleError(f"Invalid scale target: {raw!r}")
        if isinstance(raw, (int, float)):
            return cls(factor=float(raw))
        if isinstance(raw, str):
            if m := _FACTOR_RE.match(raw):
                return cls(factor=float(m.group(1)))
            if m := _COUNT_TABLE_RE.match(raw):
                return cls(rows={m.group(2): parse_count(m.group(1))})
            raise ScaleError(
                f"Invalid scale target: {raw!r} (expected e.g. '10x', '1M users', or a table map)"
            )
        if isinstance(raw, dict):
            if "factor" in raw and len(raw) == 1:
                return cls(factor=float(raw["factor"]))
            if "rows" in raw and len(raw) == 1 and isinstance(raw["rows"], dict):
                return cls(rows={k: parse_count(v) for k, v in raw["rows"].items()})
            return cls(rows={str(k): parse_count(v) for k, v in raw.items()})
        raise ScaleError(f"Invalid scale target: {raw!r}")

    @property
    def label(self) -> str:
        if self.factor is not None:
            return f"{_trim(self.factor)}x"
        return ",".join(f"{t}={format_count(n)}" for t, n in self.rows.items())


class ResolvedScale(BaseModel):
    """A scale target resolved into exact row counts for every table."""

    label: str
    factor: float = Field(description="Effective multiplier vs the baseline (total rows ratio)")
    rows: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


class ScalePlan(BaseModel):
    baseline_rows: dict[str, int] = Field(
        description="Row counts the factors are relative to (source estimates)"
    )
    targets: list[ResolvedScale]

    @property
    def baseline_total(self) -> int:
        return sum(self.baseline_rows.values())

    def target(self, label: str) -> ResolvedScale | None:
        for t in self.targets:
            if t.label == label:
                return t
        return None


def baseline_rows_for(schema: Schema, base_rows: int) -> dict[str, int]:
    """Source cardinality per table; empty/unknown tables fall back to ``base_rows``."""
    return {t.name: (t.estimated_rows if t.estimated_rows > 0 else base_rows) for t in schema.tables}


def resolve_scale(targets: list[ScaleTarget], schema: Schema, base_rows: int = 1000) -> ScalePlan:
    """Turn user targets into exact per-table row counts.

    * Uniform factor: every table is multiplied by the factor.
    * Explicit rows: listed tables take the given count; unlisted tables stay
      at the source baseline. ``{users: 10M}`` grows ``users`` only.
    """
    baseline = baseline_rows_for(schema, base_rows)
    if not baseline:
        raise ScaleError("Schema has no tables to scale")
    resolved: list[ResolvedScale] = []
    seen_labels: set[str] = set()
    for target in targets:
        rows: dict[str, int] = {}
        if target.factor is not None:
            if target.factor <= 0:
                raise ScaleError("Scale factor must be positive")
            rows = {name: max(1, int(round(n * target.factor))) for name, n in baseline.items()}
        else:
            unknown = [t for t in target.rows if schema.table(t) is None]
            if unknown:
                raise ScaleError(
                    f"Scale target references unknown table(s): {', '.join(unknown)}. "
                    f"Known tables: {', '.join(sorted(baseline))}"
                )
            explicit = {schema.table(t).name: n for t, n in target.rows.items()}  # type: ignore[union-attr]
            for name, n in baseline.items():
                rows[name] = max(1, explicit[name] if name in explicit else n)
        total = sum(rows.values())
        factor = total / max(sum(baseline.values()), 1)
        label = target.label
        if label in seen_labels:
            raise ScaleError(f"Duplicate scale target: {label}")
        seen_labels.add(label)
        resolved.append(ResolvedScale(label=label, factor=factor, rows=rows))
    resolved.sort(key=lambda r: r.total_rows)
    return ScalePlan(baseline_rows=baseline, targets=resolved)
