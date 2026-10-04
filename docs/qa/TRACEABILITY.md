# Requirement traceability

Each row is a product requirement, the test that locks it, and the result from the full `pytest` run (170 passed).

| Requirement | Test | Result |
|---|---|---|
| Minimal config loads with documented defaults | `test_defaults` | pass |
| Missing config file | `test_missing_file`, `test_missing_and_malformed_config` | pass |
| Malformed / non-mapping / empty YAML | `test_config_edges` | pass |
| Invalid field types and empty scale targets | `test_invalid_config_is_reported`, `test_config_edges` | pass |
| Env substitution, default, and missing variable | `test_expand_env`, `test_load_yaml_and_resolve_files` | pass |
| Missing env error does not leak other secrets | `test_missing_env_error_names_the_variable_not_other_secrets` | pass |
| Duplicate query names fail before sandbox work, exit 2 | `test_duplicate_query_names`, `test_duplicate_query_names_are_configuration_errors` | pass |
| Missing query file fails before sandbox work, exit 2 | `test_missing_query_file`, `test_missing_query_file_exits_as_configuration_error` | pass |
| Query file is read as a path, including odd names | `test_query_file_with_shell_metacharacters_is_a_path` | pass |
| Scale factors, explicit counts, implied factor, duplicates | `test_scale` | pass |
| Negative counts rejected | `test_scale_rejects_negative_and_clamps_explicit_zero` | pass |
| Explicit 0 rows becomes 1 | `test_scale_rejects_negative_and_clamps_explicit_zero` | pass |
| Larger factor never yields fewer rows | `test_larger_factor_never_reduces_rows_and_plans_are_deterministic` | pass |
| Huge factors stay exact integer math | `test_large_factor_is_exact_integer_math_and_stays_ordered` | pass |
| Empty schema cannot be scaled | `test_scale_rejects_negative_and_clamps_explicit_zero` | pass |
| Generation plan is deterministic for a seed | `test_larger_factor_never_reduces_rows_and_plans_are_deterministic` | pass |
| FK specs reference a planned parent | `test_foreign_keys_point_at_planned_parents` | pass |
| Schema topology, uniqueness, type categories | `test_schema` | pass |
| Adapter surface and registry | `test_adapter_contract` | pass |
| Identifiers with quotes and spaces are quoted | `test_unusual_identifiers_are_quoted_in_ddl` | pass |
| Postgres DDL, keys, enums, FK `NOT VALID` | `test_postgres_sql` | pass |
| Source connection is read-only | `test_source_connection_is_read_only_and_sandbox_is_destroyed`, `test_read_only_connection_refuses_writes` | pass |
| Source catalog unchanged after a run | `test_source_database_is_untouched` | pass |
| Unknown database type fails before sandbox start | `test_unknown_database_type_fails_before_the_sandbox_starts` | pass |
| No tables fails before sandbox start | `test_empty_schema_fails_before_the_sandbox_starts` | pass |
| Populate failure destroys the sandbox | `test_populate_failure_destroys_sandbox_and_does_not_return_a_partial_result` | pass |
| One query failure keeps the others; status `partial` | `test_failed_query_is_partial_and_successful_query_is_kept`, `test_query_error_stops_that_query_only`, integration pipeline | pass |
| Every query failing yields status `failed` and still returns | `test_every_query_failing_marks_the_experiment_failed_but_returns_results` | pass |
| Warmup is not a sample; explain failure keeps latency | `test_warmup_is_not_counted_as_a_sample`, `test_explain_failure_keeps_latency` | pass |
| Same experiment, same finding ids | `test_same_inputs_produce_the_same_finding_ids` | pass |
| Seq scan boundary 99,999 / 100,000 | `test_sequential_scan_boundary` | pass |
| Selective scan severity bump; unfiltered lowered | `test_sequential_scan_boundary` | pass |
| Excessive-scan finding dropped when the seq scan explains it | `test_excessive_scan_is_dropped_when_the_seq_scan_already_explains_it` | pass |
| Healthy scaling is not called non-linear | `test_scaling_patterns` | pass |
| Sudden degradation is non-linear | `test_scaling_patterns` | pass |
| One scale does not invent an exponent | `test_scaling_patterns`, `test_insufficient_evidence_does_not_invent_a_scaling_exponent` | pass |
| p95 threshold: at the value no breach, just over is medium | `test_threshold_boundaries` | pass |
| Index scan, sort, join, aggregation, timeout findings | `test_analysis` | pass |
| Recommendations match findings and do not duplicate indexes | `test_rules_advisor` | pass |
| LLM payload is structured; bad JSON and bad enums are safe | `test_llm_advisor`, `test_llm_contract_rejects_garbage_and_keeps_structure` | pass |
| AI provider failure keeps deterministic findings | `test_ai_failure_does_not_discard_deterministic_results` | pass |
| AI disabled does not call the provider | `test_ai_disabled_does_not_call_a_provider` | pass |
| Report with no findings does not invent an index | `test_report_with_no_findings_does_not_invent_a_risk` | pass |
| JSON round-trip, including Unicode | `test_json_round_trip`, `test_result_round_trip_preserves_unicode` | pass |
| Malformed and unknown plans | `test_malformed_and_unknown_plans` | pass |
| Docker CLI missing, daemon error without the password, bad port | `test_sandbox` | pass |
| `sandbox.type: url` without a URL | `test_url_sandbox_without_url_fails_before_any_container` | pass |
| CLI init, version, exit codes, `--fail-on`, interrupt | `test_cli` | pass |
| Inspect does not populate and does not print the password | `test_inspect_is_read_only_and_hides_the_password` | pass |
| Passwords redacted in connect and LLM errors | `test_security`, `test_anthropic_provider_reads_text_blocks_and_redacts_errors` | pass |
| Result JSON and CLI output omit the source password | `test_run_writes_json_without_passwords_and_fail_on_respects_severity` | pass |
| Ecommerce pipeline: missing index, healthy index, broken SQL | `test_full_pipeline_detects_known_bottlenecks` | pass |
| External sandbox cleaned up unless kept | `test_external_sandbox_is_cleaned_up_unless_kept` | pass |
| Postgres inspect, filters, timeout, SQL errors | `test_postgres_adapter` | pass |

Unmapped on purpose: live LLM calls, million-row generation, `SIGKILL` cleanup, `CHANGE_SCHEMA` / `PARTITION` emission. See [QA_REPORT.md](QA_REPORT.md).
