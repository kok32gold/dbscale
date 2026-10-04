# Architecture overview

DBScale is a pipeline. Each stage consumes a plain data model produced by the previous one, and only
the adapter layer knows which database it is talking to.

```
source database ──read-only──▶ Schema ──▶ GenerationPlan ──▶ sandbox database
                                │                                   │
                                │          ScalePlan (1x, 10x, …)   │ populate per target
                                ▼                                   ▼
                        AnalysisContext ◀── QueryMeasurement ◀── benchmark (runs + EXPLAIN)
                                │
                                ├──▶ Findings        (deterministic analyzers)
                                ├──▶ Recommendations (rules advisor)
                                └──▶ AIAnalysis      (optional LLM advisor)
                                                │
                                                ▼
                                       ExperimentResult → terminal / JSON
```

## Layers

| Package | Responsibility | Knows about SQL dialects? |
|---|---|---|
| `core/` | Data models: `Schema`, `ScalePlan`, `Workload`, `PlanSummary`, `Finding`, `Recommendation`, `ExperimentResult`, config | No |
| `generation/` | Turns a `Schema` into a `GenerationPlan`: for every column, a `ValueSpec` describing the distribution to generate | No |
| `adapters/` | `DatabaseAdapter` contract and implementations. Inspect, reproduce, populate, execute, explain | Yes (only here) |
| `infrastructure/` | Sandbox lifecycle: Docker container or external URL | Docker only |
| `benchmark/` | Repeated execution, latency statistics, plan collection | No |
| `analysis/` | Analyzers over normalized plans and latencies → findings and scaling exponents | No |
| `advisors/` | Rules advisor (findings → recommendations); LLM advisor (results → interpretation) | No |
| `experiments/` | `ExperimentRunner`: the orchestration and progress reporting | No |
| `reporting/` | Terminal rendering and JSON serialization | No |
| `cli/` | Typer commands | No |

## The run, step by step

1. **Connect read-only.** The adapter opens the source with a read-only session and refuses any write.
2. **Inspect.** Tables, columns, types, PK/FK/unique constraints, indexes, row estimates and per-column
   statistics (null fraction, distinct count, average width, top frequencies, correlation). No row values
   unless `database.sample_common_values` is on.
3. **Plan generation.** `GenerationPlanner` assigns a `ValueSpec` to every column: how keys are derived from
   the row number, which FK distribution to use (uniform vs skewed), null fractions, enum/choice weights,
   whether timestamps are ordered, text lengths, numeric ranges.
4. **Resolve scale.** `resolve_scale` turns `10x` or `{users: 10M}` into exact per-table row counts for each
   target, sorted ascending.
5. **Create sandbox.** A fresh PostgreSQL container (or a user-provided empty database).
6. **Reproduce schema.** Tables (unlogged, primary keys only), enum types.
7. **For each target:** drop indexes and FKs → truncate → populate in bulk → create indexes → create FKs
   (`NOT VALID`) → `ANALYZE` → run every query (warmup + timed runs) → collect `EXPLAIN ANALYZE`.
8. **Analyze.** Analyzers inspect each query across scales and emit findings with evidence. A log-log fit
   of p50 vs. total rows gives the scaling exponent.
9. **Recommend.** The rules advisor maps findings to concrete actions and attaches finding IDs.
10. **Optionally interpret with an LLM** (`ai.enabled` or `dbscale explain`), which only ever sees the
    structured result.
11. **Report.** Terminal summary and `dbscale-results.json`. Destroy the sandbox unless kept.

## Invariants

* **Source is read-only.** Enforced at the connection level and re-checked in the adapter.
* **Core has no dialect.** Analyzers operate on `PlanSummary` (`NodeKind`, `ScanInfo`, `SortInfo`,
  `JoinInfo`, `AggregateInfo`), not on native plan JSON. If a new analyzer needs more, extend the
  normalized model and make adapters populate it.
* **Facts and proposals are separate types.** `Finding` = observed and evidenced. `Recommendation` =
  proposed, with `confidence`, `expected_impact`, `tradeoffs`, and the `finding_ids` it rests on.
* **Deterministic.** Given the same schema, seed and scale, generated data is identical and analyzers
  produce the same findings. Latencies vary; everything derived from plan shape does not.
* **Stable output.** `ExperimentResult` is versioned (`format_version`). See
  [experiments/results.md](../experiments/results.md).

## What does not belong

Each stage has a job and a job it must refuse. The benchmark measures. It does
not choose a recommendation. The rules advisor proposes. It does not re-time a
query. The LLM explains a saved result. It does not open a database. The full
table is [layers.md](layers.md).

Missing metrics stay missing. Adapters declare [capabilities](capabilities.md).
`null` is not coerced to `0`.

## Extension points

* New database: implement `DatabaseAdapter` (see [adapter-contract.md](adapter-contract.md)).
* New analyzer: subclass `analysis.Analyzer`; add it to `DEFAULT_ANALYZERS` or pass it to
  `ExperimentRunner(analyzers=...)`.
* New recommendation rule: add to `advisors/rules/advisor.py`.
* New LLM provider: implement `LLMProvider.complete(system, user) -> str` in `advisors/llm/providers.py`.
* New sandbox type: implement `infrastructure.Sandbox` (`start() -> url`, `destroy()`).

The map of contracts is [extensions.md](extensions.md).
