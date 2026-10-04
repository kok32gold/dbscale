"""LLM advisor: interprets structured experiment results.

The model receives schema metadata, workload SQL, measurements, normalized
plan facts, deterministic findings and rule recommendations. It never gets
database access and is instructed not to invent measurements.
"""

from __future__ import annotations

import json
import re
from typing import Any

from dbscale.advisors.llm.providers import LLMProvider, LLMProviderError
from dbscale.core.experiment import AIAnalysis, ExperimentResult
from dbscale.core.findings import Severity
from dbscale.core.measurements import PlanNode
from dbscale.core.recommendations import Impact, Recommendation, RecommendationType

SYSTEM_PROMPT = """You are DBScale's database performance advisor.

You receive the structured results of a scalability experiment: a database schema, SQL workloads,
latency measurements at several synthetic data sizes, normalized execution-plan facts, deterministic
findings with evidence, and rule-based recommendations.

Rules you must follow:
1. Use only the supplied evidence. Never invent measurements, row counts, or plan nodes.
2. Clearly separate observed facts from hypotheses. Mark hypotheses as such.
3. State uncertainty honestly; assign a confidence between 0 and 1 to each recommendation.
4. Prefer concrete, actionable recommendations (exact DDL, specific rewrites) over generic advice.
5. Explain tradeoffs (write amplification, storage, staleness, complexity).
6. Do not recommend changes unsupported by evidence. If the data is insufficient, say so.
7. Prioritize: the most severe, most certain issues first.
8. Where useful, suggest follow-up experiments DBScale could run (e.g. "add index X and re-run").

Respond with ONLY a JSON object of this shape (no prose outside the JSON):
{
  "summary": "2-5 sentence plain-language assessment for a developer",
  "recommendations": [
    {
      "query_name": "name of the workload query or null",
      "type": "ADD_INDEX | CHANGE_INDEX | REWRITE_QUERY | CHANGE_SCHEMA | PARTITION | PRE_AGGREGATE | INVESTIGATE",
      "severity": "info | low | medium | high | critical",
      "title": "short title",
      "explanation": "why, referencing the evidence (facts) and marking hypotheses",
      "proposed_action": "exact DDL / rewrite / step",
      "expected_impact": "low | medium | high | significant | unknown",
      "confidence": 0.0,
      "tradeoffs": "what it costs",
      "finding_ids": ["ids of findings this is based on"]
    }
  ],
  "hypotheses": ["statements that are plausible but not proven by the evidence"],
  "follow_up_experiments": ["concrete experiments to validate recommendations"]
}
"""


class LLMAdvisor:
    def __init__(self, provider: LLMProvider, *, max_plan_nodes: int = 40):
        self.provider = provider
        self.max_plan_nodes = max_plan_nodes

    def advise(self, result: ExperimentResult) -> AIAnalysis:
        payload = self.build_payload(result)
        user = "Experiment results (JSON):\n" + json.dumps(payload, indent=1, default=str)
        try:
            raw = self.provider.complete(SYSTEM_PROMPT, user)
        except LLMProviderError as exc:
            return AIAnalysis(
                provider=self.provider.name, model=self.provider.model, summary="", error=str(exc)
            )
        return self.parse_response(raw, provider=self.provider.name, model=self.provider.model)

    # ------------------------------------------------------------ payload

    def build_payload(self, result: ExperimentResult) -> dict[str, Any]:
        schema = result.schema_model
        return {
            "experiment": result.name,
            "database": {"type": result.database.type, "version": result.database.version},
            "scale": {
                "baseline_total_rows": result.scale.baseline_total,
                "targets": [
                    {
                        "label": t.label,
                        "factor": round(t.factor, 3),
                        "total_rows": t.total_rows,
                        "rows": t.rows,
                    }
                    for t in result.scale.targets
                ],
            },
            "thresholds": result.thresholds.model_dump(exclude_none=True),
            "schema": [
                {
                    "table": t.name,
                    "source_rows": t.estimated_rows,
                    "columns": [
                        {"name": c.name, "type": c.native_type, "nullable": c.nullable} for c in t.columns
                    ],
                    "primary_key": t.primary_key,
                    "indexes": [
                        {
                            "name": ix.name,
                            "columns": ix.columns,
                            "unique": ix.unique,
                            "predicate": ix.predicate,
                        }
                        for ix in t.indexes
                        if not ix.primary
                    ],
                    "foreign_keys": [
                        {
                            "columns": fk.columns,
                            "references": f"{fk.referenced_table}({', '.join(fk.referenced_columns)})",
                        }
                        for fk in t.foreign_keys
                    ],
                }
                for t in schema.tables
            ],
            "workloads": [self._workload_payload(w) for w in result.workloads],
        }

    def _workload_payload(self, w) -> dict[str, Any]:
        measurements = []
        for m in w.measurements:
            entry: dict[str, Any] = {
                "scale": m.scale_label,
                "factor": round(m.scale_factor, 3),
                "total_rows": m.total_rows,
                "error": m.error,
            }
            if m.latency:
                entry["latency_ms"] = {
                    "p50": round(m.latency.p50_ms, 2),
                    "p95": round(m.latency.p95_ms, 2),
                    "p99": round(m.latency.p99_ms, 2),
                }
            entry["rows_returned"] = m.rows_returned
            if m.plan:
                entry["rows_scanned"] = m.plan.rows_scanned
                entry["scans"] = [
                    {
                        "kind": s.kind.value,
                        "table": s.relation,
                        "index": s.index_name,
                        "rows_scanned": s.rows_scanned,
                        "rows_output": s.rows_output,
                        "filter": s.filter,
                        "index_condition": s.index_condition,
                        "loops": s.loops,
                    }
                    for s in m.plan.scans
                ]
                entry["sorts"] = [s.model_dump() for s in m.plan.sorts]
                entry["joins"] = [j.model_dump() for j in m.plan.joins]
                entry["aggregates"] = [a.model_dump() for a in m.plan.aggregates]
                entry["plan_tree"] = self._compact_plan(m.plan.root)
            measurements.append(entry)
        return {
            "name": w.query.name,
            "sql": w.query.sql,
            "measurements": measurements,
            "scaling": w.scaling.model_dump() if w.scaling else None,
            "findings": [f.model_dump(mode="json") for f in w.findings],
            "rule_recommendations": [
                r.model_dump(mode="json", exclude={"evidence"}) for r in w.recommendations
            ],
        }

    def _compact_plan(self, root: PlanNode) -> list[str]:
        lines: list[str] = []

        def walk(node: PlanNode, depth: int) -> None:
            if len(lines) >= self.max_plan_nodes:
                return
            parts = [node.node_type]
            if node.relation:
                parts.append(f"on {node.relation}")
            if node.index_name:
                parts.append(f"using {node.index_name}")
            parts.append(f"rows={int(node.total_actual_rows):,}")
            if node.rows_removed_by_filter:
                parts.append(f"removed={int(node.total_rows_removed):,}")
            if node.loops > 1:
                parts.append(f"loops={node.loops}")
            if node.actual_total_ms is not None:
                parts.append(f"time={node.total_time_ms:.1f}ms")
            if node.filter:
                parts.append(f"filter={node.filter}")
            if node.index_condition:
                parts.append(f"cond={node.index_condition}")
            if node.sort_keys:
                parts.append(f"sort={','.join(node.sort_keys)} ({node.sort_method or ''})")
            lines.append("  " * depth + " ".join(parts))
            for c in node.children:
                walk(c, depth + 1)

        walk(root, 0)
        return lines

    # ------------------------------------------------------------ parsing

    @staticmethod
    def parse_response(raw: str, *, provider: str, model: str) -> AIAnalysis:
        data = _extract_json(raw)
        if data is None:
            return AIAnalysis(
                provider=provider,
                model=model,
                summary=raw.strip()[:2000],
                raw_response=raw,
                error="Response was not valid JSON; stored as free text",
            )
        recs: list[Recommendation] = []
        for n, item in enumerate(data.get("recommendations") or [], start=1):
            if not isinstance(item, dict):
                continue
            recs.append(
                Recommendation(
                    id=f"ai:R{n}",
                    type=_enum(
                        RecommendationType, str(item.get("type", "")).upper(), RecommendationType.INVESTIGATE
                    ),
                    severity=_enum(Severity, str(item.get("severity", "")).lower(), Severity.MEDIUM),
                    title=str(item.get("title") or "Recommendation")[:200],
                    explanation=str(item.get("explanation") or ""),
                    proposed_action=str(item.get("proposed_action") or ""),
                    confidence=_clamp(item.get("confidence")),
                    expected_impact=_enum(
                        Impact, str(item.get("expected_impact", "")).lower(), Impact.UNKNOWN
                    ),
                    query_name=item.get("query_name") or None,
                    finding_ids=[str(x) for x in (item.get("finding_ids") or []) if x],
                    tradeoffs=item.get("tradeoffs") or None,
                    source="llm",
                )
            )
        return AIAnalysis(
            provider=provider,
            model=model,
            summary=str(data.get("summary") or "").strip(),
            recommendations=recs,
            hypotheses=[str(h) for h in (data.get("hypotheses") or []) if h],
            follow_up_experiments=[str(e) for e in (data.get("follow_up_experiments") or []) if e],
            raw_response=raw,
        )


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _enum(enum_cls, value: str, default):
    try:
        return enum_cls(value)
    except ValueError:
        return default


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5
