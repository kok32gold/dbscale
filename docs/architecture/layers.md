# What each layer may not do

The pipeline is one direction. A later stage may read the previous stage's
model. It may not reach back into a database, and it may not do the next
stage's job.

```text
Experiment → Adapter → Schema → Synthetic data → Workload
    → Benchmark → Measurements → Findings → Recommendations → Optional AI
```

| Layer | It does | It does not |
| --- | --- | --- |
| Experiment runner | Order the stages, report progress, attach errors | Invent SQL, decide a recommendation, call an LLM unless AI is enabled |
| Adapter | Talk to one database | Decide which finding matters, or scale policy |
| Schema model | Hold tables, keys, indexes, statistics | Store row values (except opt-in common values) |
| Synthetic data planner | Choose distributions from statistics | Insert rows, or read the source again |
| Workload | Name the SQL the user asked to run | Generate that SQL, or rewrite it |
| Benchmark | Time executions and collect a plan when the adapter can | Interpret latency, or skip a failed query's siblings |
| Measurements | Record facts, including "this fact is missing" | Fill a missing plan with zeros |
| Findings | State what was observed, with evidence | Propose DDL |
| Recommendations | Propose an action tied to finding ids | Claim the action was measured |
| AI advisor | Explain a saved result | Re-run the benchmark, or open a database connection |
| Reporter | Render terminal text and JSON | Change a finding |

## Consequences for contributors

- A slow query is a benchmark fact. The recommendation to add an index is a
  rules-advisor fact. They are different types.
- If PostgreSQL can show rows scanned and another database cannot, the second
  adapter sets `capabilities.rows_scanned = False` and leaves `rows_scanned`
  null. Analyzers skip that fact. They do not assume zero.
- An AI failure is stored on `ai.error`. The findings from the run stay.

See [capabilities.md](capabilities.md) and [extensions.md](extensions.md).
