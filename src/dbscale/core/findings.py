"""Findings: deterministic observations backed by evidence.

A finding is an *observed fact* ("this query performs a sequential scan over
48M rows to return 47"). It never proposes an action; that is a recommendation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank < other.rank

    @classmethod
    def max(cls, items: list[Severity]) -> Severity | None:
        return max(items, key=lambda s: s.rank) if items else None


_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class FindingType(str, Enum):
    SEQUENTIAL_SCAN = "sequential_scan"
    EXCESSIVE_ROWS_SCANNED = "excessive_rows_scanned"
    LARGE_SORT = "large_sort"
    EXPENSIVE_JOIN = "expensive_join"
    AGGREGATION_BOTTLENECK = "aggregation_bottleneck"
    NON_LINEAR_SCALING = "non_linear_scaling"
    GROWING_LATENCY = "growing_latency"
    THRESHOLD_BREACH = "threshold_breach"
    EXECUTION_FAILURE = "execution_failure"
    DISK_SPILL = "disk_spill"


class Finding(BaseModel):
    id: str
    type: FindingType
    severity: Severity
    title: str
    description: str
    query_name: str | None = None
    scale_label: str | None = Field(default=None, description="Scale at which the evidence was observed")
    table: str | None = None
    columns: list[str] = Field(default_factory=list, description="Columns implicated (filter/sort/join keys)")
    evidence: dict[str, Any] = Field(default_factory=dict)
