# Findings and recommendations

DBScale keeps two things apart:

* a **finding** is something that was *observed* in a measurement, with the evidence attached;
* a **recommendation** is something DBScale *proposes* you do about one or more findings, with a
  confidence, an expected impact and the trade-offs.

Both are deterministic: the same plans and latencies always produce the same findings and the same
rule-based recommendations. The optional LLM advisor adds a third, clearly labeled layer
(`source: "llm"`) on top; see [../ai/advisor.md](../ai/advisor.md).

## Finding types

| Type | Observed when | Key evidence |
|---|---|---|
| `sequential_scan` | A sequential scan of ≥ 100k rows (smaller scans stay unreported while their severity would be INFO) | `rows_scanned`, `rows_returned`, `filter`, `order_by_columns`, `loops` |
| `excessive_rows_scanned` | The query scans ≥ 100k rows and ≥ 1000× more than it returns (and no single seq scan already explains it) | `rows_scanned`, `rows_returned`, `ratio` |
| `large_sort` | A sort over ≥ 100k rows, or any sort that spilled to disk | `rows`, `method`, `space_kb`, `space_type`, `keys` |
| `expensive_join` | A nested loop that re-reads a large inner side, or a hash join that spilled to multiple batches | `inner_loops`, `inner_rows_scanned`, `hash_batches`, `inner_kind` |
| `aggregation_bottleneck` | An aggregate over ≥ 1M input rows | `input_rows`, `output_rows`, `strategy`, `group_keys` |
| `non_linear_scaling` | Fitted exponent > 1.15 | `exponent`, p50 at first/last scale, `projected_next_10x_ms` |
| `growing_latency` | Exponent between 0.6 and 1.15 on a query that is already slow | same |
| `threshold_breach` | p95/p99/rows-scanned/exponent above a configured threshold | `breaches[]`, `worst_ratio` |
| `execution_failure` | The query errored or timed out at some scale | `error`, scale |
| `disk_spill` | Reserved; spills are currently reported through `large_sort` / `expensive_join` evidence | — |

Analyzers only look at the **largest scale** where the pattern appears, so a query gets one finding per
pattern, not one per scale.

### Severity

Severity is driven by the amount of work, then adjusted:

| Rows involved | Base severity |
|---|---|
| ≥ 10M | HIGH |
| ≥ 1M | MEDIUM |
| ≥ 100k | LOW |

* A scan that keeps < 0.1% of what it reads is bumped one level (it is the textbook "missing index").
* A scan with no filter at all (the query really needs every row) is lowered one level.
* Threshold breaches: MEDIUM below 2× the limit, HIGH up to 4×, CRITICAL above.
* `execution_failure` is HIGH.

The experiment's overall **risk** is the highest severity among its findings.

## Recommendation types

| Type | Produced by |
|---|---|
| `ADD_INDEX` | Seq scan with filter columns (plus same-table `ORDER BY` columns to also serve the sort); large sort whose keys belong to a single table; nested loop whose inner side is a seq scan on the join key |
| `CHANGE_INDEX` | Reserved for cases where an existing index should be altered (currently surfaced as `INVESTIGATE` with the existing index named) |
| `REWRITE_QUERY` | Unfiltered full reads that are not aggregations; sorts over computed expressions |
| `CHANGE_SCHEMA`, `PARTITION` | Available types for advisors; the rules advisor does not yet emit them |
| `PRE_AGGREGATE` | Aggregation bottleneck over a large table |
| `INVESTIGATE` | Execution failures, hash joins spilling to disk, findings no rule could turn into a concrete action |

Each recommendation carries:

* `proposed_action` — copy-pasteable where possible, e.g.
  `CREATE INDEX CONCURRENTLY "idx_orders_user_id_created_at" ON "public"."orders" ("user_id", "created_at");`
* `finding_ids` — the findings it rests on. Findings not attached to any recommendation are still
  reported; they are never silently dropped.
* `confidence` (0–1) and `expected_impact` (`low`, `medium`, `high`, `significant`, `unknown`).
* `tradeoffs` — write amplification, storage, staleness for pre-aggregation, etc.

Duplicate `ADD_INDEX` proposals (same table and columns from different queries) are merged. If an index
with a matching prefix already exists, the advisor says so instead of proposing another one.

## Reading a report

```
QUERY  recent-orders                         ⚠ HIGH RISK

  1x         42ms
  10x        91ms
  100x      1.82s     p95 2.1s · 48,921,331 rows scanned → 47 returned

Latency grows roughly linearly with the data (p50 ~ data^0.82).

Findings
  HIGH     Sequential scan on orders
           At 100x, 'orders' is read sequentially: 48,921,331 rows scanned to keep 47.

Recommended actions
  HIGH     Add index on orders (user_id, created_at)  [ADD_INDEX]
           CREATE INDEX CONCURRENTLY ...
           expected impact: significant · confidence: 70%
```

The findings block states what happened; the actions block states what to try. `dbscale run -v` shows
every finding including INFO ones; `--fail-on high` makes the command exit non-zero for CI.
