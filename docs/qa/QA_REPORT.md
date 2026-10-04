# DBScale QA report

Run on the working tree after the tests in this change. Commands:

```text
pytest tests/unit tests/stress          # 159 passed
pytest tests/integration                # 11 passed (Docker, PostgreSQL 16)
pytest                                  # 170 passed, 0 failed
pytest --cov=dbscale --cov-branch --cov-report=term-missing tests
ruff check src tests                    # clean
```

Integration used a disposable `postgres:16-alpine` container. No test was skipped.

## Results

| | |
|---|---|
| Tests executed | 170 |
| Passed | 170 |
| Failed | 0 |
| Statement coverage | 92.8% (2,846 / 3,067) |
| Branch-aware coverage | 90% (coverage.py, `--cov-branch`) |
| Function coverage | Not measured as its own figure. Statement and branch numbers above are the measured ones. |

90% is not 100%. The missing lines are listed below as known gaps, not as untested product areas that were forgotten.

## Defects fixed while testing

These were found by writing the failure tests, then fixed in the implementation:

| Defect | Fix |
|---|---|
| A negative row count (`-1`, `-1.5`) was accepted and later clamped | `parse_count` rejects negatives |
| `ExperimentResult.status` stayed `completed` when a query failed | `partial` when some measurements fail, `failed` when every measurement fails. The result is still returned |
| Duplicate query names and a missing query file aborted as a generic exit 1 | They are `ConfigError`s. `dbscale run` exits 2 and does not start a sandbox |
| Connection errors and LLM errors could echo `user:password@host` or `Bearer sk-…` | `redact_secrets` scrubs URL passwords, bearer tokens, and `sk-` tokens |
| `sandbox keep` printed the sandbox URL, password included | The note prints the redacted URL. For Docker, the container name is included so the container can still be found |

The documented sequential-scan threshold now matches the code: scans under 100,000 rows are not reported while their severity would be INFO. That behavior was already in the analyzer; the docs said 10,000.

## What was validated

* Config: missing file, empty file, non-mapping YAML, bad types, empty targets, unknown sandbox type, env substitution, duplicate query names, missing query file, Unicode names.
* Scale: factors, explicit counts, geometric mean for unlisted tables, duplicate labels, unknown tables, empty schema, negative rejection, explicit zero raised to 1, `1.5x`, and factors up to 10,000x as integer math.
* Generation: dependency order, keys, foreign keys, skew, composite keys, self-references, null fractions, determinism for a fixed seed. Foreign-key specs point at a planned parent.
* Benchmark: warmup excluded from samples, one query's error does not stop the next, explain failure keeps latency.
* Analysis: sequential scan at 99,999 vs 100,000, selective bump, unfiltered lower severity, excessive-scan dedupe, healthy vs sudden scaling, single-scale does not invent an exponent, threshold just under and just over p95.
* Rules and LLM: existing advisor tests, plus malformed JSON, non-object JSON, extra fields, confidence clamp, Anthropic text extraction, provider failure.
* Orchestration without Docker: source connection is read-only, sandbox `destroy` runs after a populate failure, empty schema and unknown database type fail before `start`, repeated runs keep the same finding ids, AI failure keeps the deterministic result.
* CLI: init, overwrite, version, exit 2 / 3 / 130, `--fail-on`, inspect, report, explain `--no-save`.
* Secrets: passwords absent from result JSON and CLI output.
* Docker integration: ecommerce inspect, reproduce, 1x and 10x, missing-index recommendation, indexed lookups without a sequential-scan finding, broken SQL preserved beside successful queries, external sandbox cleanup, source catalog unchanged.

## Known gaps

These are real. They are not covered by an automated test that would fail if the behavior regressed.

| Gap | Why it is open |
|---|---|
| Live OpenAI / Anthropic call | Providers are mocked. A bad production API change would not be caught here. |
| Postgres connect timeout and a dropped connection mid-query | The adapter translates `QueryCanceled` and driver errors. Those branches were not hit against a server that hangs. |
| Docker image pull, port collision, ready-timeout, and `docker rm` | Failure paths are mocked. The happy path and cleanup run for real in integration. |
| Generating millions of rows | Stress tests check scale arithmetic only. Memory and disk behavior at that size is unverified. |
| `CHANGE_SCHEMA`, `PARTITION`, and a standalone `disk_spill` finding | Types exist. The rules advisor does not emit the first two. Spills are attached to sort and hash-join findings. |
| Several rule branches | Expression filters, hash-join spill recommendations, and a sequential scan whose table is missing from the schema have no fixture. Rules coverage is 80%. |
| Query-file confinement | `../` outside the config directory is allowed on purpose. The test checks that a shell-looking name is still a path. |
| Column `native_type` injection | Type strings come from PostgreSQL `format_type` and are not re-quoted, because they include typmods. Identifiers are quoted. |
| Process kill (`SIGKILL`) during populate | `KeyboardInterrupt` is tested at the CLI. A kill -9 cannot run `finally`. |
| Concurrent runs | The runner is single-threaded. No concurrency contract. |
| Function coverage as a separate percentage | Not collected. Use the statement and branch figures. |

## Remaining risk

The highest residual risk is the PostgreSQL data generator (`synth.py`, 76% branch-aware). Integration proves the ecommerce schema loads, satisfies the queries, and keeps foreign keys usable at 10x. It does not prove every `ValueKind` against a server. Unsupported types are planned as NULL or a best-effort cast and noted; a silent wrong value for an untested type is still possible.

Source safety for the ecommerce fixture is checked by a catalog snapshot in `test_source_database_is_untouched`. That does not cover every catalog object PostgreSQL can hold (policies, triggers, extensions).
