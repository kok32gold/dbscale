# Workloads

A workload is the set of statements DBScale runs at every scale target.

```yaml
workload:
  kind: sql          # the only kind today
  runs: 5            # timed executions
  warmup: 1          # discarded executions, so the first timed run is warmer
  timeout_ms: 120000
  queries:
    - name: recent-orders
      file: queries/recent-orders.sql
      description: optional
    # or inline:
    - name: count-users
      sql: SELECT count(*) FROM users
```

Names must be unique. They show up in finding ids (`recent-orders:F1`).

## What a workload is not

The workload does not generate data, choose scale, or interpret plans. It is
the user's SQL. DBScale does not rewrite it. If you want a different shape of
work later (a transaction script, a mixed read/write mix), add a `kind` and
an executor that still returns `QueryMeasurement`s. Analyzers should keep
working on those measurements.

## Limits in the PostgreSQL adapter

- One statement per query. A trailing semicolon is stripped.
- No bind parameters yet. Use literals. With `sample_common_values: true`,
  low-cardinality literals from the source statistics appear in the synthetic data.
- A timeout or error becomes an `execution_failure` finding for that query.
  Other queries still run.

See [experiments/configuration.md](../experiments/configuration.md).
