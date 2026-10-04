# PostgreSQL adapter

`src/dbscale/adapters/postgres/` — the reference adapter. Tested against PostgreSQL 16; anything ≥ 12
should work (it uses only `pg_catalog`, `pg_stats`, `generate_series`, `EXPLAIN (FORMAT JSON)`).

## Connecting

```yaml
database:
  type: postgres            # aliases: postgresql, pg
  connection: ${DATABASE_URL}
  schemas: [public]         # optional; default: public
  include_tables: [orders, order_*]   # optional fnmatch patterns
  exclude_tables: [audit_log]
```

The source connection is opened with `autocommit=True`, `options=-c default_transaction_read_only=on`
and `connection.read_only = True`. Any write attempt fails on the server (`cannot execute … in a
read-only transaction`), and the adapter itself refuses to call reproduction methods on a read-only
connection. The user only needs `CONNECT` plus read access to the catalog and `pg_stats` (which already
hides columns the role cannot select).

Required privileges for useful statistics: the tables should have been `ANALYZE`d at some point
(autovacuum normally takes care of this). If `pg_stats` is empty for a table, DBScale falls back to
defaults (5% nulls, uniform FKs) and says so in the plan notes.

## What is reproduced

* Schemas, tables, columns with their native types (including arrays, `numeric(p,s)`, `varchar(n)`,
  `timestamptz`, `uuid`, `jsonb`, `bytea`, user enums, ranges fall back to text)
* Primary keys (single and composite), foreign keys (single, composite, self-referencing), unique
  constraints, secondary indexes via `pg_get_indexdef` (btree/hash/gin/gist/brin, partial indexes,
  expression indexes)
* Enum types (`CREATE TYPE … AS ENUM`, idempotent)
* Generated (`GENERATED ALWAYS AS … STORED`) columns are recreated as generated columns

## What is simplified

| Source | Sandbox | Why |
|---|---|---|
| Column defaults, `serial`/identity | Dropped | Values are generated explicitly; sequences are irrelevant |
| Domains | Underlying base type | Keeps generation simple; constraints on domains are not checked |
| Partitioned tables | One regular table | Partition pruning behavior is therefore *not* measured — see below |
| Composite unique constraints over non-key columns | Plain (non-unique) index | The generator cannot guarantee uniqueness of random tuples |
| Logged tables | `UNLOGGED` (`sandbox.unlogged_tables: true`) | Loads 2–3× faster; WAL is irrelevant in a disposable DB |
| Validated FKs | `NOT VALID` FKs | Generated data is valid by construction; validation would just re-scan everything |
| Check constraints, triggers, RLS policies, rules | Not reproduced | They do not affect read plans; triggers would slow loads |
| Views, materialized views, functions | Not reproduced | Queries referencing them will fail with `execution_failure` — inline them in the workload for now |
| Extensions (`postgis`, `pgvector`, …) | `CREATE EXTENSION` when the sandbox image includes it. Otherwise the column is stored as `text`, generated expressions that need the type are dropped, and indexes that need the type or access method are skipped | A missing type used to abort `CREATE TABLE`. Queries that call the original operators still fail at execution and are reported as `execution_failure`. Built-in arrays (`uuid[]`, `integer[]`, …) are generated. |
| Keys | Sequential integers from 1 (`uuid` via `lpad(to_hex(n), 32, '0')::uuid`, text via `'col-'||n`) | Deterministic and FK-consistent without lookups |

Sandbox server settings: the Docker sandbox runs `postgres:16-alpine` with `fsync=off`,
`full_page_writes=off`, `synchronous_commit=off`, `autovacuum=off`, `checkpoint_timeout=30min`,
`max_wal_size=4GB`, `shared_buffers=256MB`. During loads the adapter sets `jit=off`. Index and
primary-key builds use `maintenance_work_mem=128MB` and `max_parallel_maintenance_workers=2`, then
both are reset so the benchmark session is unchanged. `shared_buffers` stays small on purpose:
absolute numbers reflect a small instance, and relative numbers across scales are what matters.
Point `sandbox.type: url` at a production-like instance if absolute latency matters.

## Workload execution

* Queries run as single statements (no multi-statement scripts). Trailing semicolons are stripped.
* `workload.timeout_ms` becomes `statement_timeout` per statement; a timeout is reported as an
  `execution_failure` for that scale and the run continues.
* Latency is wall time from the client including fetching all rows. Plans come from a separate
  `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` execution after the timed runs.
* Parameterized queries are not supported yet; use literals. With
  `database.sample_common_values: true` the generated data contains the most common values of
  low-cardinality columns so literal predicates match.

## Plan mapping

| PostgreSQL node | `NodeKind` |
|---|---|
| Seq Scan | `seq_scan` |
| Index Scan | `index_scan` |
| Index Only Scan | `index_only_scan` |
| Bitmap Heap Scan / Bitmap Index Scan | `bitmap_heap_scan` / `bitmap_index_scan` |
| Nested Loop / Hash Join / Merge Join | `nested_loop` / `hash_join` / `merge_join` |
| Hash | `hash` (unwrapped so the hash join's inner side is the scan beneath it) |
| Sort, Incremental Sort | `sort` |
| Aggregate, HashAggregate, GroupAggregate, MixedAggregate, Group | `aggregate` |
| Limit | `limit` |
| Gather, Gather Merge | `gather` |
| Materialize, Memoize | `materialize` |
| Function Scan, CTE Scan, Subquery Scan, Values Scan, Tid Scan, Foreign Scan, … | `other_scan` |
| WindowAgg and anything else | `other` |

Rows scanned per node = `(actual rows + rows removed by filter) × loops`. In parallel plans PostgreSQL
reports per-worker averages with `loops = workers + 1`, so the product is the total work.

## Known gaps

* Partitioning: pruning is a major scaling tool and is currently neither reproduced nor recommended
  (`PARTITION` recommendations are reserved for a future analyzer).
* Statistics targets, extended statistics (`CREATE STATISTICS`) and custom collations are not copied.
* Tables without a primary key get scalar columns only (unique columns are still sequence-derived so FKs
  referencing a unique constraint line up).
