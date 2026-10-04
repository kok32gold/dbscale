# DBScale test plan

DBScale is a pipeline: read-only inspect, generation plan, disposable sandbox, benchmark, deterministic analysis, rule recommendations, optional LLM interpretation, then a versioned result.

Tests exist to show that pipeline is correct. Line coverage is a check, not the goal. A test stays only when it would fail if a specific behavior broke.

## Layers

| Layer | Command | What it is allowed to need |
|---|---|---|
| Unit | `pytest tests/unit` | Nothing outside the process. Adapters, Docker, and LLMs are faked. |
| Integration | `pytest tests/integration` | Docker and a disposable PostgreSQL. Skipped when Docker is absent. |
| Stress | `pytest tests/stress` | Large scale *arithmetic* only. No row allocation, no database. |
| Full | `pytest` | All of the above. Integration skips itself without Docker. |
| Coverage | `pytest --cov=dbscale --cov-branch --cov-report=term-missing tests/unit` | Unit layer. |

`ruff check src tests` and `ruff format --check src tests` gate style.

## What a failure means

| Question | Where the fix goes |
|---|---|
| Test encodes the wrong contract | Test |
| Code breaks a documented invariant | Implementation, plus a regression test |
| Spec and code disagree | Spec updated to the intended contract, or code changed to match the spec |
| Timing or model prose | Not asserted. Structure, units, and evidence are. |

## Requirement matrix

| Requirement | Where it is tested | Negative / failure case |
|---|---|---|
| Source database is read-only | `test_orchestration`, `test_postgres_adapter` | Writes on the source connection raise; workload SQL never runs there |
| Source is unchanged | `test_source_database_is_untouched` (Docker) | Snapshot of catalog before and after |
| Unknown database type fails before a sandbox starts | `test_unknown_database_type_fails_before_the_sandbox_starts` | `mongodb` |
| Empty schema fails before a sandbox starts | `test_empty_schema_fails_before_the_sandbox_starts` | No tables |
| Config errors are actionable and happen first | `test_config`, `test_cli`, `test_behavior_matrix` | Missing file, non-mapping YAML, bad types, duplicate names, missing query file (exit 2) |
| Scale targets resolve to exact row counts | `test_scale`, `test_behavior_matrix`, `test_scale_limits` | Negative rejected; explicit 0 raised to 1; duplicate labels; unknown table; empty schema |
| Generation is deterministic and referential | `test_generation`, `test_behavior_matrix` | Same seed matches; FK specs point at planned parents |
| PostgreSQL DDL quotes identifiers | `test_security`, `test_postgres_sql` | Quote and space in names |
| Populate / benchmark failures stay local | `test_benchmark`, `test_orchestration` | One query fails, the next still runs; explain failure keeps latency |
| Experiment status | `test_orchestration`, integration pipeline | `completed`, `partial` (some queries failed), `failed` (all queries failed, result still returned) |
| Sandbox is destroyed after failure | `test_orchestration` | Populate error still calls `destroy` |
| Docker missing / daemon down / bad port | `test_sandbox` | No password in the error |
| Analyzers: true positive, true negative, boundary | `test_analysis`, `test_behavior_matrix` | Index scan silent; seq scan at 99,999 silent; 100,000 reported; healthy scaling silent; sudden degradation reported; one scale does not invent an exponent |
| Recommendations cite findings | `test_rules_advisor` | No evidence → no concrete index; duplicates merged |
| LLM is not a source of facts | `test_llm_advisor`, `test_behavior_matrix` | Malformed JSON, bad enums, extra fields, provider down. Core result kept |
| Secrets not in results, CLI, or errors | `test_security`, `test_cli`, `test_orchestration` | URL passwords and `sk-` tokens redacted |
| CLI exit codes | `test_cli` | 0 success, 1 overwrite, 2 config, 3 `--fail-on`, 130 interrupt |
| Result JSON round-trips | `test_experiment_result`, `test_behavior_matrix` | Unicode names and SQL |
| Adapter contract | `test_adapter_contract` | Incomplete adapter cannot be constructed; registry aliases |

## End-to-end scenarios

| # | Scenario | Automated by |
|---|---|---|
| 1 | Inspect → generate → benchmark → report | Integration `test_full_pipeline_detects_known_bottlenecks`; unit CLI `run` on the recording adapter |
| 2 | Missing index is detected and an index is recommended | Integration pipeline, `missing-index` query |
| 3 | Indexed lookup does not produce a sequential-scan finding | Integration pipeline, `indexed-lookup` / `sku-lookup` |
| 4 | Latency that jumps at the largest scale is non-linear | `test_scaling_patterns` |
| 5 | One broken statement does not drop successful measurements | Integration pipeline (`broken`) and `test_failed_query_is_partial_and_successful_query_is_kept` |
| 6 | AI disabled | `test_ai_disabled_does_not_call_a_provider`; integration runs with `ai.enabled: false` |
| 7 | AI enabled, provider returns structure | `test_llm_advisor` |
| 8 | AI provider down, deterministic result kept | `test_ai_failure_does_not_discard_deterministic_results` |
| 9 | Database type / empty schema / Docker missing | Unit tests listed above. Live "Postgres refuses the password" is covered as a redacted `OperationalError`, not a full server |
| 10 | Interrupt | CLI `KeyboardInterrupt` → exit 130. The runner's `finally` destroys a sandbox that already started |
| 11 | Source safety | Integration snapshot plus unit assertion that source calls are only connect / server_info / inspect / close |
| 12 | Multi-table ecommerce project | Integration pipeline (users, orders, products, order items, categories, events) |

## Explicitly not claimed

* Exact millisecond timings. Latency assertions are structural (sample count, percentiles on synthetic samples) or relative (exponent class).
* Exact LLM sentences.
* A live OpenAI or Anthropic call.
* Multi-million-row generation inside CI. Stress tests check integer scale math only.
* Concurrent experiments. The runner is single-threaded; there is no concurrency contract.
* `disk_spill` as its own finding. Spills are reported through sort and hash-join evidence. The enum value is reserved.
* `CHANGE_SCHEMA` and `PARTITION` recommendations. The rules advisor does not emit them yet.
* Query files confined to the config directory. Paths are files, not shell commands. `../` is intentionally allowed so a config can point at a shared query. Shell metacharacters in a file name are still just a path.
* Column `native_type` re-quoting. Types come from `format_type` and include typmods (`character varying(255)`), so they are interpolated as type syntax. Identifiers (tables, columns, constraints) are quoted.

## Invariants locked by these tests

* Same schema, seed, and scale → same generation plan and same finding ids.
* A larger scale factor never resolves to fewer rows than a smaller one.
* A scaling exponent is absent when fewer than two distinct factors succeeded.
* Passwords in `user:password@host` URLs and `Bearer` / `sk-` tokens do not survive error text, the result JSON, or CLI output.
* `--fail-on` compares severity rank and does not fail the process when the worst finding is below the bar.
