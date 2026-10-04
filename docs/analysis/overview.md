# Analysis

Analysis is deterministic. It reads measurements and writes findings. It does
not call a model and it does not suggest a fix. Suggestions live in the rules
advisor; see [concepts/findings-and-recommendations.md](../concepts/findings-and-recommendations.md).

Built-in analyzers:

| Analyzer | Looks at |
| --- | --- |
| `ExecutionFailureAnalyzer` | query errors and timeouts |
| `SequentialScanAnalyzer` | large sequential scans |
| `ExcessiveRowsScannedAnalyzer` | rows scanned versus rows returned |
| `LargeSortAnalyzer` | large or spilled sorts |
| `ExpensiveJoinAnalyzer` | nested loops and spilled hash joins |
| `AggregationAnalyzer` | large aggregates |
| `ScalingAnalyzer` | how p50 grows with data |
| `ThresholdAnalyzer` | `thresholds` in `dbscale.yaml` |

Plan-based analyzers no-op when `plan` is null or when `rows_scanned` is null.
Latency and failure findings still run. That is how a database without plan
support stays useful.

To add one, follow [contributing/analyzers.md](../contributing/analyzers.md).
