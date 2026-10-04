"""Recommendations: *proposed actions* derived from findings.

Always structured, never free text alone. ``source`` records whether the
recommendation came from deterministic rules or from an LLM advisor.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from dbscale.core.findings import Severity


class RecommendationType(str, Enum):
    ADD_INDEX = "ADD_INDEX"
    CHANGE_INDEX = "CHANGE_INDEX"
    REWRITE_QUERY = "REWRITE_QUERY"
    CHANGE_SCHEMA = "CHANGE_SCHEMA"
    PARTITION = "PARTITION"
    PRE_AGGREGATE = "PRE_AGGREGATE"
    INVESTIGATE = "INVESTIGATE"


class Impact(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SIGNIFICANT = "significant"
    UNKNOWN = "unknown"


class Recommendation(BaseModel):
    id: str
    type: RecommendationType
    severity: Severity
    title: str
    explanation: str
    proposed_action: str = Field(description="Concrete action, e.g. a DDL statement or a rewrite hint")
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    expected_impact: Impact = Impact.UNKNOWN
    query_name: str | None = None
    table: str | None = None
    columns: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    source: str = Field(default="rules", description="'rules' or 'llm'")
    tradeoffs: str | None = None
