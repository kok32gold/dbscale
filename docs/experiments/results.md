# Results file format

`dbscale run` writes `dbscale-results.json` (`output.json`, or `-o`). It is the complete record of an
experiment and the input to `dbscale report` and `dbscale explain`. The schema is defined by the pydantic
models in `src/dbscale/core/experiment.py`; this page describes the shape and the stability guarantees.

## Stability

* `format_version` (currently `1`) is bumped only for breaking changes: renaming or removing a field, or
  changing a field's meaning. Adding fields does not bump it.
* Enum values (`type`, `severity`, `kind`, …) are lowercase snake_case strings, except
  `RecommendationType` which is uppercase (`ADD_INDEX`) to match the spec vocabulary.
* Field order is not significant. Null means "not available" (e.g. `latency: null` when the query failed,
  `rows_scanned: null` when the adapter did not report rows read). `0` is a measured zero.
* Timestamps are ISO-8601 in UTC.

## Top level

```jsonc
{
  "format_version": 1,
  "dbscale_version": "0.1.0",
  "name": "ecommerce-demo",
  "started_at": "2026-10-04T09:34:57.945100Z",
  "finished_at": "2026-10-04T09:35:18.422042Z",
  "status": "completed",                 // completed | failed | partial
  "database": {
    "type": "postgres", "version": "16.1",
    "source_tables": 6, "source_rows": 57520,
    "sandbox": "docker postgres:16-alpine",
    "capabilities": {
      "execution_plans": true,
      "rows_scanned": true,
      "index_information": true,
      "buffer_statistics": true,
      "column_statistics": true
    }
  },
  "schema": { ... },                     // normalized Schema (tables, columns, keys, indexes, stats)
  "scale": { ... },                      // ScalePlan: baseline + resolved targets
  "thresholds": { "p95_ms": 100.0, "p99_ms": null, "max_rows_scanned": null, "max_scaling_exponent": null },
  "workloads": [ ... ],                  // one WorkloadResult per query
  "findings": [ ... ],                   // all findings, flattened
  "recommendations": [ ... ],            // all recommendations, flattened
  "ai": null,                            // AIAnalysis when the LLM advisor ran
  "metadata": { "generation_notes": [], "workload": { "runs": 5, "warmup": 1, "timeout_ms": 60000 } }
}
```

## `schema`

The inspected source schema. `tables[]` have `name`, `schema_name`, `columns[]`, `primary_key`,
`foreign_keys[]`, `indexes[]`, `unique_constraints[]`, `estimated_rows`. Each column carries
`data_type` (normalized), `native_type`, `nullable`, `default`, `max_length`, `numeric_precision/scale`,
`enum_values`, `is_generated`, and `stats` (`null_fraction`, `distinct_count`, `avg_width`,
`top_frequencies`, `correlation`, and `common_values` — only populated with `sample_common_values`).

## `scale`

```jsonc
{
  "baseline_rows": { "users": 2000, "orders": 10000, ... },
  "targets": [
    { "label": "1x",   "factor": 1.0,   "rows": { "users": 2000,   "orders": 10000,   ... } },
    { "label": "10x",  "factor": 10.0,  "rows": { "users": 20000,  "orders": 100000,  ... } },
    { "label": "100x", "factor": 100.0, "rows": { "users": 200000, "orders": 1000000, ... } }
  ]
}
```

`rows` are the exact counts loaded (verified against the sandbox after each load).

## `workloads[]`

```jsonc
{
  "query": { "name": "missing-index", "sql": "SELECT ...", "file": "queries/missing_index.sql", "description": null },
  "measurements": [ /* one per scale, in scale order */ ],
  "scaling": {
    "exponent": 0.347,                   // p50 ~ rows^exponent (log-log least squares); null if not fittable
    "first_label": "1x", "last_label": "100x",
    "first_p50_ms": 6.12, "last_p50_ms": 30.28,
    "projected_next_10x_ms": 67.35
  },
  "findings": [ ... ],                   // this query's findings
  "recommendations": [ ... ]             // this query's recommendations
}
```

### `measurements[]`

```jsonc
{
  "query_name": "missing-index",
  "scale_label": "100x", "scale_factor": 100.0, "total_rows": 5752000,
  "latency": { "runs": 5, "min_ms": 25.8, "max_ms": 34.1, "mean_ms": 30.1, "p50_ms": 30.3, "p95_ms": 33.5, "p99_ms": 34.0 },
  "samples_ms": [34.08, 30.28, 25.79, 31.32, 28.91],
  "rows_returned": 4,
  "plan": {
    "planning_ms": 0.045, "execution_ms": 25.96,
    "rows_returned": 4,
    "rows_scanned": 999999,              // Σ over scans of (rows output + rows removed) × loops
    "scans": [ { "kind": "seq_scan", "relation": "orders", "alias": "orders", "index_name": null,
                 "rows_scanned": 999999, "rows_output": 3, "rows_removed_by_filter": 999996, "loops": 3,
                 "filter": "(user_id = 42)", "index_condition": null, "time_ms": 35.5 } ],
    "sorts":  [ { "keys": ["created_at DESC"], "rows": 3, "method": "quicksort", "space_kb": 25, "space_type": "Memory", "time_ms": 35.8 } ],
    "joins":  [ /* kind, join_type, condition, rows, outer_rows, inner_rows, inner_rows_scanned, inner_loops, inner_kind, inner_relation, hash_batches, time_ms */ ],
    "aggregates": [ /* strategy, group_keys, input_rows, output_rows, disk_kb, time_ms */ ],
    "shared_hit_blocks": 1545, "shared_read_blocks": 6903, "temp_written_blocks": 0,
    "root": { /* normalized PlanNode tree: kind, node_type, relation, actual_rows, loops, filter, children, ... */ }
  },
  "raw_plan": [ /* native EXPLAIN JSON; omitted when output.include_raw_plans is false */ ],
  "error": null,                         // set when execution failed; latency/plan are then null
  "plan_error": null                     // set when EXPLAIN failed but the query itself ran
}
```

`plan` is **database-agnostic**; `raw_plan` is whatever the adapter received and exists for debugging.

### `findings[]`

```jsonc
{
  "id": "missing-index:F1",              // "<query>:F<n>"; stable for a given result
  "type": "sequential_scan",
  "severity": "medium",                  // info | low | medium | high | critical
  "title": "Sequential scan on orders",
  "description": "At 100x, 'orders' is read sequentially: 999,999 rows scanned to keep 3 ...",
  "query_name": "missing-index",
  "scale_label": "100x",                 // the scale the evidence comes from
  "table": "orders",
  "columns": ["user_id"],
  "evidence": { "rows_scanned": 999999, "rows_output": 3, "selectivity": 3e-06, "loops": 3,
                "filter": "(user_id = 42)", "order_by_columns": ["created_at"],
                "latency_p50_ms": 30.28, "latency_p95_ms": 33.53, ... }
}
```

Evidence keys depend on the finding type (see
[../concepts/findings-and-recommendations.md](../concepts/findings-and-recommendations.md)); they are
always plain JSON scalars/lists so tooling can consume them.

### `recommendations[]`

```jsonc
{
  "id": "missing-index:R1",              // "<query>:R<n>"; LLM ones are "ai:R<n>"
  "type": "ADD_INDEX",
  "severity": "medium",
  "title": "Add index on orders (user_id, created_at)",
  "explanation": "The query filters 'orders' on user_id but no index covers it ...",
  "proposed_action": "CREATE INDEX CONCURRENTLY \"idx_orders_user_id_created_at\" ON \"public\".\"orders\" (\"user_id\", \"created_at\");",
  "evidence": { ... },                   // copied from the primary finding
  "confidence": 0.85,
  "expected_impact": "significant",      // low | medium | high | significant | unknown
  "query_name": "missing-index",
  "table": "orders",
  "columns": ["user_id", "created_at"],
  "finding_ids": ["missing-index:F1"],
  "source": "rules",                     // rules | llm
  "tradeoffs": "Each index slows inserts/updates on the table and uses disk; ..."
}
```

## `ai`

Present after `dbscale run --ai` or `dbscale explain`:

```jsonc
{
  "provider": "openai", "model": "gpt-4o-mini",
  "summary": "...",
  "recommendations": [ /* same Recommendation shape, source = "llm" */ ],
  "hypotheses": [ "..." ],               // explicitly non-evidenced ideas, kept apart from recommendations
  "follow_up_experiments": [ "..." ],
  "raw_response": "...",
  "error": null                          // set if the provider failed; the rest of the result is unaffected
}
```

## Reading results programmatically

```python
from dbscale.reporting import load_result

result = load_result("dbscale-results.json")
for w in result.workloads:
    worst = w.measurements[-1]
    print(w.query.name, worst.latency.p95_ms if worst.latency else "failed", w.risk)
```
