import json
from datetime import UTC, datetime

import pytest

from dbscale.advisors.llm import LLMAdvisor, LLMProvider, LLMProviderError, create_provider
from dbscale.advisors.llm.advisor import SYSTEM_PROMPT
from dbscale.core.config import AIConfig
from dbscale.core.experiment import DatabaseInfo, ExperimentResult, WorkloadResult
from dbscale.core.findings import Severity
from dbscale.core.recommendations import Impact, RecommendationType
from dbscale.core.scale import ScaleTarget, resolve_scale
from dbscale.core.workload import QuerySpec
from helpers import load_plan, measurement


class FakeProvider(LLMProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


class FailingProvider(LLMProvider):
    name = "broken"
    model = "x"

    def complete(self, system: str, user: str) -> str:
        raise LLMProviderError("connection refused")


def _result(schema) -> ExperimentResult:
    plan = load_plan("seq_scan_filter_sort")
    w = WorkloadResult(
        query=QuerySpec(name="recent", sql="SELECT 1"),
        measurements=[
            measurement("recent", "1x", 1, 3, plan=plan),
            measurement("recent", "100x", 100, 22, plan=plan),
        ],
    )
    return ExperimentResult(
        dbscale_version="test",
        name="exp",
        started_at=datetime.now(UTC),
        database=DatabaseInfo(type="postgres", version="16"),
        schema=schema,
        scale=resolve_scale([ScaleTarget.parse("1x"), ScaleTarget.parse("100x")], schema),
        workloads=[w],
    )


GOOD_RESPONSE = """Here is my analysis:
```json
{
  "summary": "One query scans the whole orders table.",
  "recommendations": [
    {"query_name": "recent", "type": "add_index", "severity": "HIGH", "title": "Index orders(user_id, created_at)",
     "explanation": "Fact: 999,999 rows scanned. Hypothesis: index fixes it.", "proposed_action": "CREATE INDEX ...",
     "expected_impact": "significant", "confidence": 0.9, "tradeoffs": "write cost", "finding_ids": ["recent:F1"]},
    {"type": "nonsense", "severity": "weird", "title": "Fallbacks", "confidence": "high"}
  ],
  "hypotheses": ["Traffic is user-centric"],
  "follow_up_experiments": ["Add the index and re-run at 100x"]
}
```
"""


def test_advisor_sends_structured_payload_and_parses_response(schema):
    provider = FakeProvider(GOOD_RESPONSE)
    analysis = LLMAdvisor(provider).advise(_result(schema))
    system, user = provider.calls[0]
    assert system == SYSTEM_PROMPT
    payload = json.loads(user.split("\n", 1)[1])
    assert payload["experiment"] == "exp"
    assert {t["table"] for t in payload["schema"]} == set(schema.table_names())
    wl = payload["workloads"][0]
    assert wl["name"] == "recent" and wl["sql"] == "SELECT 1"
    assert wl["measurements"][1]["rows_scanned"] == 999_999
    assert wl["measurements"][1]["plan_tree"][0].startswith("Limit")
    # payload must never contain raw row data; it only carries metadata and aggregates
    assert "raw_plan" not in user

    assert analysis.error is None
    assert analysis.summary.startswith("One query")
    assert len(analysis.recommendations) == 2
    first, second = analysis.recommendations
    assert first.type == RecommendationType.ADD_INDEX and first.severity == Severity.HIGH
    assert first.expected_impact == Impact.SIGNIFICANT and first.confidence == 0.9
    assert first.source == "llm" and first.finding_ids == ["recent:F1"]
    assert second.type == RecommendationType.INVESTIGATE and second.severity == Severity.MEDIUM
    assert second.confidence == 0.5 and second.expected_impact == Impact.UNKNOWN
    assert analysis.hypotheses == ["Traffic is user-centric"]
    assert analysis.follow_up_experiments == ["Add the index and re-run at 100x"]


def test_non_json_response_is_kept_as_text():
    analysis = LLMAdvisor.parse_response("I think you should add an index.", provider="p", model="m")
    assert analysis.error and analysis.summary == "I think you should add an index."
    assert analysis.recommendations == []


def test_provider_errors_are_captured(schema):
    analysis = LLMAdvisor(FailingProvider()).advise(_result(schema))
    assert analysis.error == "connection refused" and analysis.recommendations == []


def test_plan_tree_is_capped(schema):
    provider = FakeProvider('{"summary": "ok", "recommendations": []}')
    LLMAdvisor(provider, max_plan_nodes=2).advise(_result(schema))
    payload = json.loads(provider.calls[0][1].split("\n", 1)[1])
    assert len(payload["workloads"][0]["measurements"][1]["plan_tree"]) == 2


def test_anthropic_provider_reads_text_blocks_and_redacts_errors(monkeypatch):
    import httpx

    from dbscale.advisors.llm.providers import AnthropicProvider

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "observed only"}, {"type": "tool", "id": "x"}]}

    monkeypatch.setattr("dbscale.advisors.llm.providers.httpx.post", lambda *a, **k: Response())
    provider = AnthropicProvider("claude", api_key="sk-live-abcdef123456")
    assert provider.complete("system", "user") == "observed only"

    def boom(*_a, **_k):
        raise httpx.ConnectError("denied Bearer sk-live-abcdef123456")

    monkeypatch.setattr("dbscale.advisors.llm.providers.httpx.post", boom)
    with pytest.raises(LLMProviderError, match="denied") as exc:
        provider.complete("system", "user")
    assert "sk-live-abcdef123456" not in str(exc.value)


def test_create_provider():
    p = create_provider(AIConfig(provider="openai", model="gpt-4o-mini", api_key="k"))
    assert p.name == "openai" and p.model == "gpt-4o-mini"
    p = create_provider(AIConfig(provider="ollama", model="llama3"))
    assert p.base_url == "http://localhost:11434/v1"
    p = create_provider(AIConfig(provider="anthropic", model="claude", api_key="k"))
    assert p.name == "anthropic"
    with pytest.raises(LLMProviderError):
        create_provider(AIConfig(provider="anthropic", model="claude"))  # key required
    with pytest.raises(LLMProviderError):
        create_provider(AIConfig(provider="skynet"))
