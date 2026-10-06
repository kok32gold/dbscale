# Scaling

A **scale target** says how big the synthetic database should be. An experiment usually has several
targets so you can see how latency *grows*, not just where it ends up.

## Two ways to express a target

### Factors

```yaml
scale:
  targets: [1x, 10x, 100x]
```

Every table is multiplied by the factor, relative to the row count observed in the source database
(`pg_class.reltuples`, or `count(*)` for very small/unanalyzed tables). `1x` therefore reproduces the
current size with synthetic data, which is a useful sanity check: if `1x` already looks very different
from production, the generator or the statistics are off.

Empty tables are given `scale.base_rows` (default 1000) rows at `1x` so every table participates.

### Explicit row counts

```yaml
scale:
  targets:
    - users: 10M
      orders: 100M
    - users: 50M
      orders: 1B
```

Numbers accept `K`, `M`, `B` suffixes and underscores (`1_000_000`). Tables not listed stay at the
source baseline (`1x`). Factors (`10x`) still multiply every table. Composite-key tables are capped at
the product of their parents' sizes (a `(order_id, product_id)` table cannot have more rows than
`orders × products`).

Targets are sorted by total rows and run in that order. Labels are `10x` or `users=10M,orders=100M` and
appear in findings, measurements and the report.

## What "scaling well" means here

For each query DBScale fits a power law through the per-target p50 latencies:

\[ \text{latency} \approx c \cdot \text{rows}^{e} \]

* `e ≈ 0` — flat: index lookups, `LIMIT` on an ordered index
* `e ≈ 1` — linear: full scans, sorts without an index
* `e > 1.15` — superlinear: usually sorts/hashes that spilled to disk, or nested loops over unindexed
  inner sides. Reported as `non_linear_scaling`.

The fit needs at least two targets with meaningful latency (≥ 50 ms somewhere) to be reported; faster
queries are simply listed as healthy. `thresholds.max_scaling_exponent` turns a high exponent into a
threshold breach.

## Practical advice

* Start with `[1x, 10x]` to validate the setup, then add the target you actually care about.
* Generation throughput is roughly 1M rows/s on a laptop for typical tables; 100M-row targets are feasible
  but take minutes and need disk in the sandbox (`--tmpfs` is used only for the socket, data lives in the
  container's filesystem).
* Scaling only one table (e.g. `events: 1B`) with the rest at today's size is often the most realistic
  experiment.
