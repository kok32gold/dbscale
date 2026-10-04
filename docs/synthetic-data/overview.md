# Synthetic data

DBScale does not copy production rows.

It reads the schema and planner statistics, then generates rows whose shape
matches those statistics: row counts, null fractions, distinct counts, skew,
and key relationships. Values are a function of the row number and a seed, so
the same inputs produce the same data and foreign keys line up without lookups.

The only opt-in that reads actual values is `database.sample_common_values`.
It copies the most common values of low-cardinality columns (status codes,
country codes) so literal predicates in your SQL match. It is off by default.
The run prints a note when it is on.

Full description: [concepts/synthetic-data.md](../concepts/synthetic-data.md).

A new generator should still emit a `GenerationPlan`. Adapters compile that
plan into whatever bulk-load the engine is good at. The planner does not
embed SQL.
