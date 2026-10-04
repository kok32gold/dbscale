# AI advisor

The AI advisor is **optional** and **additive**. Every measurement, finding and rule-based recommendation
is produced without it. It exists to explain results in plain language, prioritize across queries, and
suggest actions the rules engine is too conservative to make.

## What the model receives

A single JSON document built by `LLMAdvisor.build_payload()` from the saved `ExperimentResult`:

* experiment name, database type/version, scale targets and per-table row counts, thresholds
* schema **metadata**: table names, columns (`name`, `type`, `nullable`), primary keys, indexes, foreign keys
* per query: the SQL text, latency statistics per scale, rows returned, a compacted plan
  (up to `ai.max_plan_nodes` nodes: kind, relation, rows, loops, filter/index condition, sort/group keys),
  the scaling exponent, deterministic findings with their evidence, and rule recommendations

The model never gets: a connection string, database access, generated or source row data, `raw_plan`,
or anything not already present in `dbscale-results.json`. `dbscale explain results.json` works
completely offline from the database for exactly this reason.

If column names themselves are sensitive in your context, do not enable AI with a hosted provider; use a
local model (`provider: ollama`) or skip it.

## What the model returns

The system prompt (`SYSTEM_PROMPT` in `advisors/llm/advisor.py`) demands JSON only:

```json
{
  "summary": "2-5 sentences",
  "recommendations": [ { "query_name", "type", "severity", "title", "explanation",
                         "proposed_action", "expected_impact", "confidence", "tradeoffs", "finding_ids" } ],
  "hypotheses": ["plausible but unproven statements"],
  "follow_up_experiments": ["add index X and re-run at 100x"]
}
```

and instructs the model to use only supplied evidence, mark hypotheses as such, give confidences, prefer
concrete DDL/rewrites, and explain trade-offs.

Parsing is defensive: code fences and surrounding prose are stripped, unknown enum values fall back to
`INVESTIGATE` / `MEDIUM` / `UNKNOWN`, and confidence is clamped to `[0, 1]` (default `0.5`). A malformed or
failed response sets `ai.error`; the rest of the result is untouched and the command still succeeds.

LLM recommendations get IDs `ai:R<n>` and `source: "llm"`, so they are always distinguishable from
rule-based ones in both the terminal report and the JSON.

## Providers

```yaml
ai:
  enabled: true
  provider: openai            # see table
  model: gpt-4o-mini
  api_key: ${OPENAI_API_KEY}
  base_url: null
  timeout_s: 120
  max_plan_nodes: 40
```

| `provider` | API | Notes |
|---|---|---|
| `openai` | `POST {base_url}/chat/completions` | `base_url` defaults to `https://api.openai.com/v1` |
| `anthropic` | `POST {base_url}/v1/messages` | `api_key` required |
| `ollama` | OpenAI-compatible | `base_url` defaults to `http://localhost:11434/v1`; no key needed |
| `openai-compatible`, `openrouter`, `lmstudio`, `vllm` | OpenAI-compatible | set `base_url` (and `api_key` if required) |

Adding a provider: implement `LLMProvider` (`name`, `model`, `complete(system, user) -> str`) in
`advisors/llm/providers.py` and extend `create_provider`.

## Usage

```bash
dbscale run --ai                            # run + interpret
dbscale explain dbscale-results.json        # interpret a previous run; stores the analysis in the file
dbscale explain results.json --provider ollama --model llama3.1 --no-save
```

The terminal report shows the AI summary, its recommendations (labeled `AI`), and hypotheses in a
separate block so they are never mistaken for measured facts.
