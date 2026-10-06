# Changelog

All notable changes to DBScale are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed

- Synthetic loads no longer call `random()` or `md5`. Values are an immutable hash of the row number, tables of a million rows or more load on up to four backends, and primary keys are built after the heap load so a multi-ten-million-row run does not pin the machine on index maintenance.
- Explicit per-table targets (`users: 10M`) grow only the listed tables. Unlisted tables stay at the source baseline. Factors (`10x`) still scale every table.
- Canonical repository links now point at `kok32gold/dbscale`. CI also covers Python 3.12.

### Added

- Project mark, citation file, support policy, public roadmap, and starter issues.
- MkDocs site, GitHub Pages workflow, and a docs build in CI.
- Release workflow for `vX.Y.Z` tags. PyPI publishing stays off until the `PYPI_PUBLISH` repository variable is set.
- Dependabot, CodeQL, dependency review, and `pip-audit`.
- Adapter capabilities. A database that cannot report plans or rows scanned leaves those fields null instead of zero. PostgreSQL declares the metrics it collects.
- Contributor docs: security policy, code of conduct, governance, license rationale, dependency notes, extension guides, and focused examples.
- GitHub CI (lint, typecheck, unit tests, coverage, PostgreSQL 15 and 16 integration), issue templates, and a pull request template.

## [0.1.0] - 2026-10-04

Initial MVP.

### Added

- PostgreSQL adapter behind a generic `DatabaseAdapter` contract (read-only source connections,
  schema inspection from the catalog and `pg_stats`, schema reproduction, bulk synthetic population,
  execution and `EXPLAIN (ANALYZE, BUFFERS)` collection).
- Synthetic data generation that preserves primary/foreign key relationships (including composite and
  self-referencing keys), null fractions, distinct counts, value skew, enum labels, ordered timestamps
  and unique columns, without reading production rows. Optional `database.sample_common_values`
  to reproduce the most common values of low-cardinality columns.
- Configurable scaling: factors (`10x`) or explicit per-table row counts (`users: 10M`).
- Disposable Docker sandbox (`sandbox.type: docker`) or any empty PostgreSQL database (`sandbox.type: url`).
- Benchmark runner with warmup and repeated runs; p50/p95/p99 latency per scale.
- Deterministic analyzers: sequential scans, excessive rows scanned, large sorts, expensive joins,
  aggregation bottlenecks, non-linear scaling, threshold breaches, execution failures.
- Rules-based recommendations (`ADD_INDEX`, `REWRITE_QUERY`, `PRE_AGGREGATE`, `INVESTIGATE`, ...) with
  evidence, confidence and trade-offs.
- Optional LLM advisor for OpenAI, Anthropic, Ollama and OpenAI-compatible endpoints; receives structured
  results only.
- CLI: `dbscale init`, `inspect`, `run`, `report`, `explain`.
- Stable JSON result format (`format_version: 1`).
- Example PostgreSQL project with planted bottlenecks; unit and Docker-backed integration tests.

[Unreleased]: https://github.com/kok32gold/dbscale/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kok32gold/dbscale/releases/tag/v0.1.0
