# Synthetic data

DBScale never copies rows from the source database. It reads the schema and the statistics the database
already keeps for its own query planner, and generates data that has the same *shape*.

## What is read from the source

| Information | Source (PostgreSQL) | Used for |
|---|---|---|
| Tables, columns, types, nullability | `pg_class`, `pg_attribute`, `pg_type` | Reproducing the schema |
| Primary / foreign / unique keys, indexes | `pg_constraint`, `pg_index` | Key derivation, FK targets, index reproduction |
| Row estimates | `pg_class.reltuples` (or `count(*)` for small tables) | `1x` baseline |
| Null fraction | `pg_stats.null_frac` | Per-column `NULL` probability |
| Distinct count | `pg_stats.n_distinct` | Cardinality of generated values |
| Average width | `pg_stats.avg_width` | Text / binary length |
| Top-value frequencies | `pg_stats.most_common_freqs` | Skew (hot parents, dominant values) — *frequencies only, not the values* |
| Correlation | `pg_stats.correlation` | Whether timestamps/ids are generated in order |
| Enum labels | `pg_enum` | Enum columns (these are type definitions, not data) |

With `database.sample_common_values: true`, DBScale additionally reads `pg_stats.most_common_vals` for
columns with at most 200 distinct values. This is the only path that reads values, it is off by default,
and the run prints a note when it is on. It exists because queries like `WHERE country = 'US'` return
nothing against purely random data.

## How values are generated

Everything is a pure function of the **row number** `i` and the plan seed, so tables can be
generated independently (and in parallel) and still agree on keys. The seed is mixed into
`hashint8`, not `random()`: `random()` is volatile, which forces a single backend.

* **Primary keys**: `id = i`. UUID keys are `lpad(to_hex(i), 32, '0')::uuid`; text keys are `'col-' || i`.
  Any column can be derived from its key number the same way, which is what makes FKs line up.
* **Foreign keys**: pick a parent key `k ∈ [1, rows[parent]]`, uniformly or with a skewed distribution when
  the source statistics show that a few parents own most children (`most_common_freqs`). Self-references
  pick `k < i` so trees are acyclic. Because `k` is always within the parent's range, there are no orphans
  and FKs can be added `NOT VALID` without checking.
* **Composite keys**: `(order_id, product_id)` becomes a mixed-radix decomposition of `i - 1`, which
  guarantees uniqueness; the table is capped at `rows[orders] × rows[products]`.
* **Unique columns** (`email`, `sku`): sequence-derived so uniqueness holds at any scale. Unique indexes
  that the generator cannot guarantee (e.g. composite unique constraints mixing random columns) are
  created as plain indexes and a note is emitted.
* **Enums / choices**: weighted `CASE` over a hash of the row number (weights from statistics when available).
* **Numbers**: uniform within a range sized by type and precision, with `n_distinct` respected where
  meaningful.
* **Text**: hex of the row number, trimmed to the observed average width (or the declared `varchar(n)`).
* **Timestamps**: random within the last two years, or monotonically increasing with `i` when the column
  is strongly correlated with physical order or named like `created_at`.
* **JSON**: small objects with a few keys; **binary**: random bytes of the observed width.
* **Nulls**: `CASE WHEN <hash of i> < null_frac THEN NULL …` per column; never for key columns.

Each table is loaded with `INSERT … SELECT … FROM generate_series()` in 500k-row chunks. Tables of a
million rows or more are split across up to four backends. Primary keys and secondary indexes are
absent during the load (a heap append) and built afterwards as sorts, then `ANALYZE` runs.

## What is *not* reproduced

* Actual text content, names, addresses, free-form fields — only lengths and cardinalities.
* Correlations between columns (e.g. `shipped_at > created_at`), beyond ordered timestamps.
* Time-based clustering (bursts), geographic clustering, seasonality.
* Dead tuples, bloat, fragmentation, page layout, cache state.
* Check constraints and triggers.

These affect absolute latency. They rarely change *which* plan the planner picks or how a plan scales,
which is what DBScale measures. Treat the numbers as "same order of magnitude", and the plan shape and
scaling exponent as the primary signal.

## Determinism

`scale.seed` is mixed into every non-key hash, per table and column. Same source statistics + same
seed + same targets ⇒ identical sandbox contents. Key columns do not depend on the seed, so foreign
keys still line up.
