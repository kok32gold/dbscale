# Contributing to DBScale

DBScale belongs to its users and contributors. You do not need permission to
open a bug fix or a feature pull request.

A change is welcome when it keeps the source database read-only, stays in the
right layer, and includes a test for the behavior it changes.

## Getting started

Requirements:

- Python 3.11 or 3.12
- Docker, only for integration tests and the examples
- Git

You do not need a local PostgreSQL install, an LLM account, or a cloud account.

```bash
git clone https://github.com/kok32gold/dbscale.git
cd dbscale
python3.11 -m venv .venv
source .venv/bin/activate
make install
```

`make install` runs `pip install -e ".[dev]"`.

## Running locally

The smallest path uses the demo database. The password in that compose file
(`src`) is a throwaway for a container bound to `127.0.0.1`. It is not a
credential for anything else.

```bash
docker compose -f examples/postgres/docker-compose.yml up -d --wait
cd examples/simple-postgres
dbscale inspect
dbscale run
```

`dbscale run` inspects the demo database read-only, builds a disposable
PostgreSQL sandbox, generates synthetic data, runs the workload, and writes
`dbscale-results.json`. AI stays off unless you pass `--ai` or set
`ai.enabled: true`.

Other commands: `dbscale init`, `dbscale report dbscale-results.json`,
`dbscale explain dbscale-results.json`.

## Running tests

| Suite | Command | Needs |
| --- | --- | --- |
| Unit | `pytest tests/unit` | nothing |
| Stress | `pytest tests/stress` | nothing |
| Integration (end to end) | `pytest tests/integration` | Docker |
| Full suite | `pytest` | Docker; integration skips without it |
| Coverage | `pytest tests/unit tests/stress --cov=dbscale --cov-branch --cov-report=term-missing` | nothing |

`make test-unit`, `make test-integration`, and `make coverage` run the same
commands. Integration tests start `postgres:16-alpine` unless
`DBSCALE_TEST_PG_IMAGE` is set (CI also runs `postgres:15-alpine`).

There is no separate browser or hosted end-to-end suite. `tests/integration`
is the end-to-end experiment against a real PostgreSQL.

## Code quality

```bash
ruff check src tests          # lint
ruff format src tests         # format
ruff format --check src tests # format check (what CI runs)
mypy src                      # typecheck
```

Or `make lint`, `make format`, `make typecheck`.

## Architecture

```text
Experiment
    ↓
Adapter          source inspect + sandbox reproduce / execute
    ↓
Schema           normalized, dialect-free
    ↓
Synthetic data   GenerationPlan (what values look like)
    ↓
Workload         SQL the user supplied
    ↓
Benchmark        timings and plans
    ↓
Measurements
    ↓
Findings         deterministic analyzers
    ↓
Recommendations  rules advisor
    ↓
Optional AI      interpretation only
```

| Package | Owns | Does not own |
| --- | --- | --- |
| `core/` | Models and config | SQL dialects, Docker, HTTP, recommendations |
| `adapters/` | One database | Findings, scale policy, prompts |
| `generation/` | Column distributions | How rows are inserted |
| `infrastructure/` | Sandbox start/stop | Schema or SQL |
| `benchmark/` | Repeated execution | What the numbers mean |
| `analysis/` | Findings from measurements | Suggested DDL |
| `advisors/rules/` | Recommendations from findings | New measurements |
| `advisors/llm/` | Optional interpretation | The experiment result |
| `experiments/` | Ordering the stages | Database-specific SQL |
| `reporting/` | Terminal and JSON | Decisions |
| `cli/` | Commands | Business rules |

Detail: [docs/architecture/overview.md](docs/architecture/overview.md),
[docs/architecture/layers.md](docs/architecture/layers.md),
[docs/architecture/extensions.md](docs/architecture/extensions.md).

## Pull requests

Use the pull request template. Say what problem the change solves, what
changed, how you tested it, and whether docs, compatibility, or privacy are
affected.

Keep the diff limited to the layer you are changing. Adding an analyzer should
not require edits to the PostgreSQL adapter, the benchmark runner, or an AI
provider.

## Commits

Write the subject in the imperative mood and say why. `Fix read-only source
check on external sandboxes` is enough. There is no required trailer format.

## Tests

Every meaningful behavior change needs a test.

- Adapter contract surface: `tests/unit/test_adapter_contract.py`
- Plan and measurement facts: `tests/unit/test_measurements.py` and
  `tests/fixtures/plans/`
- Analyzers and rules: `tests/unit/test_analysis.py`, `tests/unit/test_rules_advisor.py`
- Pipeline without Docker: `tests/unit/test_orchestration.py` using `tests/fakes.py`
- Real PostgreSQL: `tests/integration/`

Fixtures:

- `tests/fixtures/schemas/ecommerce.sql` — source schema for integration tests
- `tests/fixtures/queries/` — workload SQL
- `tests/fixtures/plans/` — `EXPLAIN (ANALYZE, FORMAT JSON)` captured from PostgreSQL

To add a plan fixture, run `EXPLAIN (ANALYZE, FORMAT JSON)` against a synthetic
database, save the JSON, and load it with `helpers.load_plan`. Do not commit a
plan from a database that contains private SQL or private identifiers you would
not publish.

## Documentation

User-facing behavior belongs in `docs/` and, if it changes what a user types,
in `README.md` or the relevant example. Note breaking changes in `CHANGELOG.md`.

## New database adapter

Full guide: [docs/contributing/adapters.md](docs/contributing/adapters.md).

1. Implement `DatabaseAdapter` in `src/dbscale/adapters/<name>/`.
2. Map the native catalog to `Schema`. Read statistics, not rows, unless
   `sample_common_values` is on.
3. Reproduce the schema in the sandbox (tables and primary keys first).
4. Compile `GenerationPlan` into a bulk load. Keys must be a pure function of
   the row number.
5. Execute the workload. Map native plans to `PlanNode` only for facts you
   actually observed.
6. Set `capabilities` to the metrics you collect. Leave the rest false.
7. Register with `register_adapter`.
8. Add contract coverage and integration tests.
9. Write `docs/databases/<name>.md`, including what you do not support.

Connecting is not a complete adapter.

## New analyzer

Full guide: [docs/contributing/analyzers.md](docs/contributing/analyzers.md).

Subclass `Analyzer`, read only `AnalysisContext`, and append the class to
`DEFAULT_ANALYZERS` or pass it to `ExperimentRunner(analyzers=...)`. Do not
import an adapter, start Docker, or call an LLM. If the fact you need is not
on `PlanSummary`, extend the normalized model and teach adapters to fill it.
Leave it unset when an adapter cannot observe it.

## New AI provider

Full guide: [docs/contributing/ai-providers.md](docs/contributing/ai-providers.md).

Implement `LLMProvider.complete(system, user) -> str` and teach
`create_provider` to construct it. The provider receives text and returns text.
It does not get a database connection. Tests must mock HTTP. Do not put a real
API key in CI.

## Other extension points

| You want to add | Start here |
| --- | --- |
| Data generator | `generation/planner.py` produces a `GenerationPlan`. A new strategy should still emit `ValueSpec`s. |
| Workload type | `core/workload.py` (`kind` is `sql` today). The benchmark calls `adapter.execute`. |
| Recommendation rule | `advisors/rules/advisor.py` |
| Output format | `reporting/`. JSON is the stable artifact (`format_version`). |
| Sandbox | `infrastructure/sandbox.py`, `Sandbox.start() -> url` |

## Privacy rules that are not optional

- The source connection is read-only. Writes must fail on the server.
- Do not read row values unless `database.sample_common_values` is set, and
  then only the most common values of low-cardinality columns.
- Do not add telemetry. Do not send schemas, SQL, or measurements anywhere
  except an AI provider the user explicitly enabled.
- Redact passwords and API tokens in errors (`dbscale.redact`).

## Reporting issues

Starter tasks: [docs/contributing/starter-issues.md](docs/contributing/starter-issues.md).
What the project is not going to become: [ROADMAP.md](ROADMAP.md).

Use the GitHub issue templates. Include the DBScale version, database version,
operating system, and Docker version. A minimal `dbscale.yaml` and a
`dbscale-results.json` help. That file has schema metadata and plans, not row
data. Strip anything you would not publish.
