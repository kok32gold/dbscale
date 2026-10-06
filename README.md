<p align="center">
  <img src="docs/assets/mark.svg" alt="DBScale" width="72" height="72">
</p>

# DBScale

[![CI](https://github.com/kok32gold/dbscale/actions/workflows/ci.yml/badge.svg)](https://github.com/kok32gold/dbscale/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

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

## Try it

Requirements: Python 3.11+, Docker. No account, no API key, no PyPI package yet.

```bash
git clone https://github.com/kok32gold/dbscale.git
cd dbscale
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
docker compose -f examples/postgres/docker-compose.yml up -d --wait
cd examples/simple-postgres && dbscale run
```

More examples: [examples/README.md](examples/README.md) (missing index, scaling risk, several workloads, optional AI).

Install the default branch into another environment:

```bash
pip install "dbscale @ git+https://github.com/kok32gold/dbscale.git"
```

`pip install dbscale` does not work until the first PyPI release. See [docs/getting-started/install.md](docs/getting-started/install.md).

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

## Your own database

```bash
dbscale init
export DATABASE_URL=postgresql://user:pass@host:5432/mydb
```

Configure the experiment in `dbscale.yaml`:

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

Example report:

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

## Compared with the usual tools

| Question | EXPLAIN | pgbench | DBScale |
| --- | --- | --- | --- |
| What does this SQL do at today's size? | Yes | No | Yes |
| What does this SQL do at 10x and 100x? | No | Only on pgbench's own schema | Yes, on a synthetic copy of your schema |
| Whose SQL? | Yours | Built-in scripts | Yours |
| Does it change the source database? | No | No | No. The source connection is read-only. |
| Does it tell you what to change? | You read the plan | Throughput numbers | Findings and proposed DDL. Nothing is applied. |

## What it does not do

- It does not read production rows. It reads the schema and statistics. The only opt-in that reads values is `database.sample_common_values`.
- It does not replay production traffic.
- It does not apply recommendations. The `CREATE INDEX` in the report is a proposal.
- It does not measure partition pruning. Partitioned tables are loaded as one table.
- It does not run parameterized SQL yet. Use literals.
- Absolute latency comes from a small disposable Postgres unless you set `sandbox.type: url` to a larger empty database.
- PostgreSQL is the only adapter today.

The longer version is [ROADMAP.md](ROADMAP.md).

## AI

AI is optional.

DBScale can use an LLM (OpenAI, Anthropic, Ollama or any OpenAI-compatible endpoint) to explain findings, prioritize issues, and recommend concrete actions. The LLM receives structured results only — never database access.

The benchmark itself does not require AI.

## Privacy

DBScale does not send telemetry. It does not require an account, a cloud project, or an AI provider.

Your source database is opened **read-only**. DBScale reads the schema and statistical shape (row counts, null fractions, distinct counts, value frequencies), not production rows. Benchmarking runs against an isolated synthetic database in a disposable Docker container. AI, if you turn it on, receives the structured result and never a connection string.

## Supported databases

Currently:

* PostgreSQL 15 and 16 in CI. The adapter uses catalog features that are older than 15; see [docs/databases/postgres.md](docs/databases/postgres.md).

The architecture is designed to support additional databases through adapters. See [docs/architecture/](docs/architecture/overview.md).

## Status

**0.1.0, pre-1.0.** APIs and the result format can still change. Breaking changes are called out in [CHANGELOG.md](CHANGELOG.md). See [docs/versioning.md](docs/versioning.md).

The changelog records 0.1.0. A Git tag and a PyPI release are separate steps and are not done until they exist on GitHub and PyPI.

## Docs

| | |
| --- | --- |
| Install and first run | [docs/getting-started/install.md](docs/getting-started/install.md), [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md) |
| All docs | [docs/README.md](docs/README.md) |
| Configuration | [docs/experiments/configuration.md](docs/experiments/configuration.md) |
| When something fails | [docs/troubleshooting/common.md](docs/troubleshooting/common.md) |
| Support | [SUPPORT.md](SUPPORT.md) |

A GitHub Pages build is in `.github/workflows/docs.yml`. It goes live at `https://kok32gold.github.io/dbscale/` after Pages is set to deploy from GitHub Actions.

## Contributing

Contributions are welcome without prior permission: adapters, generators, analyzers, AI providers, docs, and tests. See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/contributing/starter-issues.md](docs/contributing/starter-issues.md).

Security reports: [SECURITY.md](SECURITY.md). Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

[Apache-2.0](LICENSE). You may use, modify, and redistribute DBScale, including in commercial products and private forks, under the license terms. There is no fee and no non-commercial restriction.

Why this license, and which dependency licenses apply: [docs/license.md](docs/license.md), [docs/dependencies.md](docs/dependencies.md).
