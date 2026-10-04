# Adding a database adapter

Read [adapter-contract.md](../architecture/adapter-contract.md) and the
PostgreSQL adapter in `src/dbscale/adapters/postgres/` before writing code.
PostgreSQL is the reference, not a template you must copy line for line.

## Steps

1. **Implement the contract.** Subclass `DatabaseAdapter`. `connect(url, read_only=True)`
   must make writes fail inside the database, not only inside Python.
2. **Map the catalog to `Schema`.** Tables, columns, primary keys, foreign keys,
   unique constraints, indexes you can see. Statistics: null fraction, distinct
   count, average width, frequencies. Do not `SELECT` row values unless
   `sample_common_values=True`, and then only low-cardinality most-common values.
3. **Reproduce the schema.** `create_schema` builds tables and primary keys.
   Secondary indexes and foreign keys come later, after the load.
4. **Execute the workload.** `execute` returns elapsed milliseconds and rows
   returned, or raises `AdapterError` and leaves the connection usable.
5. **Collect plans where the engine supports them.** Map native nodes to
   `NodeKind`. Unknown nodes become `NodeKind.OTHER`.
6. **Declare capabilities.** Set only the flags you honor. See
   [capabilities.md](../architecture/capabilities.md).
7. **Register.** Call `register_adapter` from the package `__init__.py`, and
   import that package from `dbscale.adapters` if it ships in-tree.
8. **Test.**
   - `tests/unit/test_adapter_contract.py` checks the abstract surface.
   - Add a unit test that a plan fixture normalizes to the expected `PlanSummary`.
   - Add `tests/integration/test_<name>_adapter.py` marked `integration`.
     Skip cleanly when Docker (or the engine) is absent.
9. **Document limitations** in `docs/databases/<name>.md`: version tested,
   what is reproduced, what is simplified, which capabilities are false.

## Not done yet

An adapter that can `SELECT 1` and nothing else is not done. Say which of
schema reproduction, synthetic load, plans, and indexes are missing.

## Fixtures

Integration tests may start a container the way
`tests/integration/conftest.py` starts PostgreSQL. Put a synthetic schema in
`tests/fixtures/schemas/`. Do not point tests at a shared remote database.
