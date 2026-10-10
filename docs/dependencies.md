# Dependencies

Runtime dependencies are pinned loosely in `pyproject.toml` (`>=` a known-good
floor). Development dependencies are the same. Nothing is fetched from a
private registry. Clone the repository and run `make install`. That
installs the dependencies. DBScale itself is not a published pip package.

No dependency is allowed to require an account, send telemetry, or run only
in one cloud. httpx is used when *you* enable an AI provider. It is not used
during a normal benchmark.

## Runtime

| Package | Role | License | Notes |
| --- | --- | --- | --- |
| psycopg[binary] | PostgreSQL driver | LGPL-3.0 | See below. `psycopg-binary` also bundles libpq, which is under the PostgreSQL License (permissive). |
| pydantic | Config and result models | MIT | |
| PyYAML | `dbscale.yaml` | MIT | Loaded with `yaml.safe_load`. |
| typer | CLI | MIT | |
| rich | Terminal report | MIT | |
| httpx | Optional AI HTTP calls | BSD-3-Clause | Idle unless AI is enabled. |

## Development

| Package | Role | License |
| --- | --- | --- |
| pytest, pytest-cov, pytest-timeout | Tests | MIT |
| coverage | Coverage | Apache-2.0 |
| ruff | Lint and format | MIT |
| mypy | Typecheck | MIT |
| hatchling | Build backend | MIT |

## psycopg and LGPL-3.0

psycopg 3 is the library DBScale uses to speak PostgreSQL. It is a dependency,
not copied into `src/`. DBScale stays Apache-2.0.

LGPL-3.0 does not stop you from using, modifying, or selling an application
that calls psycopg. If you distribute a combined binary, the LGPL gives
recipients the right to replace the psycopg library. Shipping DBScale as
source via git or a wheel that depends on psycopg (the normal pip layout)
matches how psycopg expects to be used.

We did not vendor psycopg, and we do not statically link it into DBScale.

A permissive driver would remove this note. Switching drivers is a behavior
change and is not required for DBScale to be distributable.

## What we will not add casually

- Packages that phone home
- Packages that only install from a private index
- An SDK that imports a hosted DBScale or observability account by default
- An AI SDK that runs during import

If a dependency's license would stop someone from forking or shipping DBScale
under Apache-2.0 plus that dependency's own terms, do not add it without
writing the constraint down here first.
