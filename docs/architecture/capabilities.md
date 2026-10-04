# Adapter capabilities

Databases do not expose the same facts. DBScale records that explicitly.

```python
class AdapterCapabilities:
    execution_plans: bool = False
    rows_scanned: bool = False
    index_information: bool = False
    buffer_statistics: bool = False
    column_statistics: bool = False
```

The default is all false. PostgreSQL sets all five true because
`EXPLAIN (ANALYZE, BUFFERS)` and `pg_stats` provide them.

| Flag | When true | When false |
| --- | --- | --- |
| `execution_plans` | `explain()` returns a `PlanNode` tree | The benchmark does not call `explain`. `plan` stays null and `plan_error` says plans are unavailable. Latency is still recorded. |
| `rows_scanned` | Scan nodes set `actual_rows` | Leave `actual_rows` unset. `PlanSummary.rows_scanned` becomes null, not 0. |
| `index_information` | Schema indexes and plan `index_name` are real | Leave indexes empty and `index_name` null |
| `buffer_statistics` | Hit/read/temp blocks are copied from the engine | Leave those fields null |
| `column_statistics` | Null fraction, distinct count, frequencies come from planner stats | The generator falls back to documented defaults and records a note |

`0` means the engine reported zero. `null` means the engine did not report
the metric. Analyzers that need a missing metric return no finding. They do
not guess.

The flags are copied onto `ExperimentResult.database.capabilities` so a saved
result shows what was observable.

## PostgreSQL today

| Capability | PostgreSQL |
| --- | --- |
| Execution plans | yes |
| Rows scanned | yes |
| Index information | yes |
| Buffer statistics | yes |
| Column statistics | yes (`pg_stats`) |

A future adapter might look like:

| Capability | Database X |
| --- | --- |
| Execution plans | yes |
| Rows scanned | no |
| Index information | no |

That adapter is still useful. Latency, scaling exponents, and execution
failures still work. Index recommendations that need an index name will not
fire, because the evidence is absent.
