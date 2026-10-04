# `dbscale.yaml` reference

`dbscale init` writes a starter file. Every command takes `--config/-c` (default `./dbscale.yaml`).
Relative `file:` paths in the workload resolve against the config file's directory.

`${VAR}` and `${VAR:-default}` are expanded from the environment anywhere in the file.

```yaml
name: my-experiment                      # appears in the report and results file

database:
  type: postgres                         # adapter; postgres is the only built-in one
  connection: ${DATABASE_URL}            # SOURCE database — opened read-only, never modified
  schemas: [public]                      # schemas to inspect
  include_tables: null                   # optional list of fnmatch patterns; null = all
  exclude_tables: []                     # fnmatch patterns to skip
  sample_common_values: false            # OPT-IN: read most common values of low-cardinality
                                         # (≤200 distinct) columns so literal predicates match

scale:
  targets:                               # factors and/or explicit per-table row counts
    - 1x
    - 10x
    - users: 10M
      orders: 100M
  base_rows: 1000                        # 1x size for empty tables
  seed: 42                               # synthetic data is deterministic for a seed

workload:
  kind: sql
  runs: 5                                # timed executions per query per scale
  warmup: 1                              # untimed executions first
  timeout_ms: 120000                     # per statement; a timeout is a finding, not a crash
  queries:
    - name: recent-orders                # unique; used in finding/recommendation IDs
      file: queries/recent-orders.sql    # or: sql: SELECT ...
      description: optional text shown in the report

thresholds:                              # all optional; breaches become findings
  p95_ms: 500
  p99_ms: null
  max_rows_scanned: null
  max_scaling_exponent: null             # e.g. 1.2

sandbox:
  type: docker                           # docker | url
  image: postgres:16-alpine              # docker only
  url: null                              # url only: an EMPTY database DBScale may fill and drop
  keep: false                            # keep the container / tables after the run
  name: null                             # container name (default dbscale-sandbox-<random>)
  unlogged_tables: true                  # faster loads; no WAL
  foreign_keys: true                     # reproduce FKs (NOT VALID) after loading

ai:
  enabled: false                         # the benchmark never needs this
  provider: openai                       # openai | anthropic | ollama | openai-compatible
  model: gpt-4o-mini
  api_key: ${OPENAI_API_KEY:-}
  base_url: null                         # e.g. http://localhost:11434/v1 for ollama (default)
  timeout_s: 120
  max_plan_nodes: 40                     # cap on plan nodes sent per query

output:
  json: dbscale-results.json             # machine-readable results; null disables
  include_raw_plans: true                # embed native EXPLAIN JSON in the results
```

## Commands

| Command | What it does | Needs |
|---|---|---|
| `dbscale init [--name N] [--force]` | Write `dbscale.yaml` and `queries/example.sql` | nothing |
| `dbscale inspect [-v]` | Connect read-only and print what DBScale sees (tables, keys, indexes, stats with `-v`) | source DB |
| `dbscale run [-o results.json] [--keep-sandbox] [--ai/--no-ai] [-v] [--fail-on SEVERITY]` | The full experiment | source DB, Docker (or `sandbox.url`) |
| `dbscale report results.json [-v]` | Re-render a saved result | nothing |
| `dbscale explain results.json [--provider P] [--model M] [--no-save]` | Send a saved result to the LLM advisor and store its analysis | LLM credentials |

Exit codes: `0` ok, `1` error, `2` usage/config error, `3` `--fail-on` threshold reached.

## Choosing a sandbox

* `docker` (default): DBScale starts `postgres:16-alpine` with `fsync=off`, publishes it on a random
  localhost port, and removes it at the end (`--keep-sandbox` or `sandbox.keep: true` to keep it; the URL
  is printed). Requires the `docker` CLI and a running daemon.
* `url`: bring your own PostgreSQL. It must be an **empty database DBScale owns**: it will create and
  drop tables there. Useful in CI without Docker-in-Docker, or to benchmark on production-like hardware.
  Tables are dropped at the end unless `keep` is set.

## CI usage

```yaml
- run: dbscale run --fail-on high -o results.json
- uses: actions/upload-artifact@v4
  with: { path: results.json }
```

`results.json` contains schema metadata, statistics, generated-data plans, latencies and query plans —
but no row data from the source. Review before publishing it outside your team anyway: table and column
names and index definitions can be sensitive.
