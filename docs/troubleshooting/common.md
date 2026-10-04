# Troubleshooting

## Connection closed on the first `dbscale run` after `docker compose up`

The demo container accepts connections only after PostgreSQL finishes init.
Use `docker compose up -d --wait` so Compose waits for the healthcheck.

## `Docker CLI not found`

The default sandbox is a disposable PostgreSQL container. Install Docker, or
set `sandbox.type: url` to an empty database DBScale may create and drop
tables in. Do not point `sandbox.url` at the source database.

## `Environment variable 'DATABASE_URL' is not set`

`dbscale.yaml` referenced `${DATABASE_URL}` with no default. Export it, or
use the `${DATABASE_URL:-postgresql://...}` form the examples use.

## `No tables found`

The configured user cannot read the catalog, or `schemas` / `include_tables`
exclude everything. `dbscale inspect` shows what the read-only connection sees.

## Writes to the source fail

That is the design. The source session is read-only. Benchmarks run in the
sandbox.

## A query fails at a large scale

That scale is recorded as `execution_failure` (often a timeout). Smaller
scales and other queries are kept. Raise `workload.timeout_ms` if the
statement is legitimately slow, or treat the timeout as the finding.

## `rows scanned unavailable`

The adapter declared `execution_plans` but did not report `actual_rows`, or
plan collection failed (`plan_error` on the measurement). Latency is still
valid. Plan-based findings are omitted for that query. This is not a zero-row
scan.

## AI analysis failed

The benchmark result is unchanged. `ai.error` explains the failure (missing
key, provider down, malformed response). Re-run interpretation with
`dbscale explain dbscale-results.json` after fixing the provider. Use
`--provider ollama` for a local model. Core DBScale does not need this step.

## Integration tests skip

`pytest` skips `tests/integration` when `docker info` fails. Unit tests do
not need Docker.

## Port 55432 is in use

The demo compose file publishes the source database on `127.0.0.1:55432`.
Stop the other container or change the host port in
`examples/postgres/docker-compose.yml` and the example connection URLs together.
