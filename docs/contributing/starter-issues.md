# Starter issues

These are concrete gaps. Open one as a GitHub issue, or send a pull request. A maintainer still has to create the issue if it is not already filed.

## Good first issues

1. **Python 3.13 in CI.** Add `3.13` to the unit-test matrix in `.github/workflows/ci.yml`. Fix only what that version rejects. Leave integration tests on 3.11 unless the unit job is green.
2. **Troubleshooting note for literals.** `docs/databases/postgres.md` says parameterized queries are not supported. Add a short entry to `docs/troubleshooting/common.md` that shows a literal predicate and when `database.sample_common_values` matters. Do not implement parameters in that change.
3. **One more type-compat test.** `tests/unit/test_type_compat.py` covers the vector extension being missing. Add a case for another unsupported type the adapter already documents, and assert the schema note rather than a silent rewrite.

## Larger issues

4. **Parameterized workloads.** Accept bound parameters in `core/workload.py` and execute them through the PostgreSQL adapter. Keep the source connection read-only. Add a unit test with a fake adapter and an integration test with a real predicate.
5. **Another database adapter.** Start from [adapters.md](adapters.md). MySQL is the usual request. Connecting is not a complete adapter: schema, synthetic load, execution, and an honest capabilities block are the minimum. File this with the `adapter` label.
