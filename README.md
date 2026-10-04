# DBScale

**Know how your database will behave before it gets big.**

DBScale is an open-source database scalability experimentation framework.

It creates an isolated, synthetic version of your database, scales the data to the size you expect in the future, runs your real database workloads, and shows you where performance will break.

```text
Your Database
     │
     ▼
   DBScale
     │
     ├── Reproduce schema
     ├── Generate synthetic data
     ├── Scale database
     ├── Run workloads
     └── Find bottlenecks
          │
          ▼
      Recommendations
```

## Why?

Your database works perfectly with:

```text
100K rows
```

But what happens at:

```text
10M?
100M?
1B?
```

You usually don't know until production tells you.

DBScale lets you find out beforehand.

## Quick start

Requirements: Python 3.11+, Docker, a PostgreSQL database to inspect.

```bash
pip install dbscale            # or: pip install -e . from a clone
dbscale init
export DATABASE_URL=postgresql://user:pass@host:5432/mydb
```

Configure your database and experiment in `dbscale.yaml`:

```yaml
database:
  type: postgres
  connection: ${DATABASE_URL}

scale:
  targets: [1x, 10x, 100x]     # or explicit: - users: 10M
                               #                orders: 100M
workload:
  queries:
    - name: recent-orders
      file: queries/recent-orders.sql

thresholds:
  p95_ms: 500
```

Run:

```bash
dbscale run
```

DBScale will:

```text
✓ Connected to PostgreSQL 16.6 (read-only)
✓ Inspected schema: 4 tables, ~2.4M rows
✓ Created isolated database
✓ Reproduced schema (4 tables)
✓ Generated synthetic data at 1x
✓ Tested 1x
✓ Generated synthetic data at 10x
✓ Tested 10x
✓ Generated synthetic data at 100x
✓ Tested 100x
✓ Analyzed results: 3 findings
```

Example:

```text
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
           CREATE INDEX CONCURRENTLY "idx_orders_user_id_created_at" ON "public"."orders" ("user_id", "created_at");
           expected impact: significant · confidence: 70%
```

Full results are also written to `dbscale-results.json` for CI and tooling.

Other commands: `dbscale inspect` (what DBScale sees), `dbscale report results.json`, `dbscale explain results.json` (AI, offline).

Try it without your own database:

```bash
docker compose -f examples/postgres/docker-compose.yml up -d --wait
cd examples/simple-postgres && dbscale run
```

More examples: [examples/README.md](examples/README.md) (missing index, scaling risk, several workloads, optional AI).

## AI

AI is optional.

DBScale can use an LLM (OpenAI, Anthropic, Ollama or any OpenAI-compatible endpoint) to explain findings, prioritize issues, and recommend concrete actions. The LLM receives structured results only — never database access.

The benchmark itself does not require AI.

## Privacy

DBScale does not send telemetry. It does not require an account, a cloud project, or an AI provider.

Your source database is opened **read-only**. DBScale reads the schema and statistical shape (row counts, null fractions, distinct counts, value frequencies), not production rows. The only opt-in that reads values is `database.sample_common_values`. Benchmarking runs against an isolated synthetic database in a disposable Docker container. AI, if you turn it on, receives the structured result and never a connection string.

## Supported databases

Currently:

* PostgreSQL

The architecture is designed to support additional databases through adapters. See `docs/architecture/`.

## Status

**0.1.0, pre-1.0.** APIs and the result format can still change. Breaking changes are called out in `CHANGELOG.md`. See [docs/versioning.md](docs/versioning.md).

Contributions are welcome without prior permission: adapters, generators, analyzers, AI providers, docs, and tests. See `CONTRIBUTING.md`.

## License

[Apache-2.0](LICENSE). You may use, modify, and redistribute DBScale, including in commercial products and private forks, under the license terms. There is no fee and no non-commercial restriction.

Why this license, and which dependency licenses apply: [docs/license.md](docs/license.md), [docs/dependencies.md](docs/dependencies.md).
