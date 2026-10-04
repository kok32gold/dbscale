"""The experiment result model: the single serializable artifact DBScale produces."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from dbscale.core.findings import Finding, Severity
from dbscale.core.measurements import QueryMeasurement
from dbscale.core.recommendations import Recommendation
from dbscale.core.scale import ScalePlan
from dbscale.core.schema import Schema
from dbscale.core.workload import QuerySpec

RESULT_FORMAT_VERSION = 1


class DatabaseInfo(BaseModel):
    type: str
    version: str | None = None
    source_tables: int = 0
    source_rows: int = 0
    sandbox: str | None = Field(default=None, description="How the sandbox was provided (docker image, url)")
    capabilities: dict[str, bool] = Field(
        default_factory=dict,
        description="Adapter observation flags. False means that metric was not collected, not that it was zero.",
    )


class ThresholdSpec(BaseModel):
    p95_ms: float | None = None
    p99_ms: float | None = None
    max_rows_scanned: int | None = None
    max_scaling_exponent: float | None = Field(
        default=None, description="Flag queries whose latency grows faster than factor^exponent"
    )


class ScalingSummary(BaseModel):
    """How a query's latency responded to data growth."""

    exponent: float | None = Field(
        default=None,
        description="Fitted exponent k in latency ~ factor^k. 0 = flat, 1 = linear, >1 = superlinear",
    )
    first_label: str | None = None
    last_label: str | None = None
    first_p50_ms: float | None = None
    last_p50_ms: float | None = None
    projected_next_10x_ms: float | None = None


class WorkloadResult(BaseModel):
    query: QuerySpec
    measurements: list[QueryMeasurement] = Field(default_factory=list)
    scaling: ScalingSummary | None = None
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)

    @property
    def risk(self) -> Severity | None:
        return Severity.max([f.severity for f in self.findings])

    def measurement(self, label: str) -> QueryMeasurement | None:
        for m in self.measurements:
            if m.scale_label == label:
                return m
        return None

    @property
    def successful(self) -> list[QueryMeasurement]:
        return [m for m in self.measurements if m.ok]


class AIAnalysis(BaseModel):
    provider: str
    model: str
    summary: str
    recommendations: list[Recommendation] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    follow_up_experiments: list[str] = Field(default_factory=list)
    raw_response: str | None = None
    error: str | None = None


class ExperimentResult(BaseModel):
    format_version: int = RESULT_FORMAT_VERSION
    dbscale_version: str
    name: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = Field(default="completed", description="completed | failed | partial")
    database: DatabaseInfo
    schema_: Schema = Field(alias="schema")
    scale: ScalePlan
    thresholds: ThresholdSpec = Field(default_factory=ThresholdSpec)
    workloads: list[WorkloadResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    ai: AIAnalysis | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    @property
    def schema_model(self) -> Schema:
        return self.schema_

    @property
    def risk(self) -> Severity | None:
        return Severity.max([f.severity for f in self.findings])

    def to_json(self, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent, by_alias=True)

    @classmethod
    def from_json(cls, text: str) -> ExperimentResult:
        return cls.model_validate_json(text)
