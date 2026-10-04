# The `DatabaseAdapter` contract

`src/dbscale/adapters/base.py` defines the only interface through which DBScale touches a database.
Everything above it (`core`, `generation`, `analysis`, `advisors`, `experiments`) is dialect-free.

Two adapter instances are alive during a run:

* **source** — opened with `read_only=True`; used only for `server_info()` and `inspect_schema()`.
* **sandbox** — a normal connection to the disposable database; everything else runs here.

## Methods

| Method | Purpose | Must |
|---|---|---|
| `connect(url, *, read_only=False)` | Open a connection | `read_only=True` must make every write fail at the server level, not just by convention |
| `close()` | Release the connection | |
| `server_info()` | Type + version | |
| `inspect_schema(schemas, include_tables, exclude_tables, *, sample_common_values=False)` | Build a `Schema` | Read catalog metadata and aggregate statistics only. Read actual values only for low-cardinality columns and only when `sample_common_values=True` |
| `create_schema(schema, *, unlogged=True)` | Tables + primary keys | No secondary indexes, no foreign keys (they are added after population) |
| `drop_schema(schema)` | Undo `create_schema` | Used for `sandbox.type: url` so the user's database is left empty |
| `create_indexes(schema, plan)` | Secondary indexes | Downgrade `UNIQUE` to plain when the generation plan cannot guarantee uniqueness |
| `drop_indexes(schema)` | | |
| `create_foreign_keys(schema)` | FKs | Skip validation of existing rows (generated data is referentially valid by construction) |
| `drop_foreign_keys(schema)` | | Called before repopulating so bulk loads are not slowed by per-row checks |
| `truncate(schema)` | | |
| `populate(schema, plan, scale, progress=None)` | Generate `scale.rows[table]` rows per table | Deterministic for a given `plan.seed`; call `progress(table, done, total)` as it goes |
| `analyze(schema)` | Refresh planner statistics | |
| `row_counts(schema)` | Exact counts | Used to verify population |
| `execute(sql, *, timeout_ms)` | Run and consume rows | Return `ExecutionResult(elapsed_ms, rows_returned)`; raise `AdapterError` on error or timeout and leave the connection usable |
| `explain(sql, *, timeout_ms)` | Instrumented execution | Return `ExplainResult(root: PlanNode, planning_ms, execution_ms, raw)` |

## Data generation is specified, not implemented, by the core

`generation/plan.py` describes every column as a `ValueSpec`:

* `kind` — `KEY_REF`, `INTEGER`, `DECIMAL`, `BOOLEAN`, `TEXT`, `TIMESTAMP`, `DATE`, `TIME`, `INTERVAL`,
  `UUID`, `JSON`, `BINARY`, `ENUM`, `CHOICE`, `CONSTANT`, `NULL`
* `picker` — how a value is chosen from a key space: `SEQUENCE` (row number), `UNIFORM`, `SKEWED`
  (Zipf-like, `skew` exponent), `SELF_REF` (points at a smaller key of the same table), `RADIX`
  (one digit of a mixed-radix decomposition of the row number, for composite keys)
* distribution parameters: `distinct`, `min_value/max_value`, `decimals`, `length`, `values` + `weights`
  (+ `fallback` for the long tail), `ordered`/`span_days` for temporal columns, `p_true`, `constant`

`ColumnPlan` wraps a spec with its `null_fraction`; `TablePlan.guaranteed_unique` lists the column sets the
generator promises to keep unique (so unique indexes can be reproduced safely).

The adapter compiles these into its most efficient bulk-insert idiom. The PostgreSQL adapter compiles
to `INSERT … SELECT … FROM generate_series()` per chunk, and splits large tables across several
connections. Primary keys are dropped for that load and rebuilt with the secondary indexes. A different
database might stream rows from Python or use `COPY`. Key derivation (`key_expr`) must be a pure
function of the row number so that child tables can reference parents without lookups.

## Plan normalization

`core/measurements.py` defines `NodeKind` (seq scan, index scan, index only scan, bitmap scan, nested
loop, hash join, merge join, sort, aggregate, limit, …) and `PlanNode`. `PlanSummary.from_root()` derives
`scans`, `sorts`, `joins`, `aggregates`, `rows_scanned` and buffer counters from that tree, so analyzers
never parse native output. An adapter's job is to map its native plan to `PlanNode` as faithfully as it
can; unknown node types become `NodeKind.OTHER` and are simply ignored by analyzers.

## Capabilities

Set `capabilities` on the class. The default claims nothing. PostgreSQL sets
`execution_plans`, `rows_scanned`, `index_information`, `buffer_statistics`,
and `column_statistics`.

If `execution_plans` is false, the benchmark does not call `explain`. If you
cannot observe row counts, leave `PlanNode.actual_rows` unset. `PlanSummary`
then stores `rows_scanned: null` instead of `0`. See
[capabilities.md](capabilities.md).

Document every false flag in `docs/databases/<name>.md`. An adapter that only
connects is not complete.

## Registration

```python
from dbscale.adapters.registry import register_adapter

class MyAdapter(DatabaseAdapter):
    type = "mydb"
    ...

register_adapter(MyAdapter)
```

`database.type: mydb` in `dbscale.yaml` then selects it. `tests/unit/test_adapter_contract.py` checks
that the class implements the full surface.
