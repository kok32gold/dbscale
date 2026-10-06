# Versioning

DBScale is **0.1.0**, pre-1.0. The version in `src/dbscale/__init__.py` matches
`pyproject.toml`.

Versions follow [Semantic Versioning](https://semver.org/). While the major
version is 0, minor bumps may include breaking changes. Those changes are
listed under `BREAKING` in `CHANGELOG.md`. Patch bumps are fixes.

## What we try not to break casually

| Surface | Stability |
| --- | --- |
| CLI commands `init`, `inspect`, `run`, `report`, `explain` | Kept. New flags are optional. |
| `dbscale.yaml` keys | New keys have defaults. Removing a key is breaking. |
| `dbscale-results.json` | `format_version` is 1. Adding fields does not bump it. Renaming or removing a field does. |
| `DatabaseAdapter` | New optional behavior can be a capability flag defaulting to false. Removing a method is breaking. |
| Python imports under `dbscale.*` | Public modules listed in [architecture/extensions.md](architecture/extensions.md). Anything else can move. |

## Experimental

- AI provider names beyond `openai`, `anthropic`, `ollama`, and `openai-compatible`
- `Workload.kind` other than `sql`
- Adapters other than PostgreSQL

Experimental surfaces can change in a minor 0.x release. The changelog will say so.

## Releases

Pushing a tag `vX.Y.Z` runs `.github/workflows/release.yml`. The workflow
re-runs the unit tests and the PostgreSQL 16 integration tests, builds the
sdist and wheel, attaches build provenance, and publishes a GitHub Release.

PyPI publishing is skipped unless the repository variable `PYPI_PUBLISH` is
the string `true`. The package is not on PyPI until that is set and this
repository is a trusted publisher for the `dbscale` project. Until then,
install from git.

`0.1.0` is recorded in the changelog. The Git tag `v0.1.0` is not created
by the docs. Tag the commit that matches that changelog section, not a later
commit that also contains `[Unreleased]` work.

1. Update `CHANGELOG.md` (move `Unreleased` to a version and date).
2. Set `__version__` and `pyproject.toml`.
3. Wait until CI is green on that commit.
4. Tag `vX.Y.Z` and push the tag.

There is no private release pipeline. If a tag exists, the commit in this
repository is the release.
