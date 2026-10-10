# Roadmap

This is the public direction, not a commitment and not a date. The changelog is the record of what shipped. Pre-1.0, a minor version may break a contract listed in [docs/versioning.md](docs/versioning.md).

## Now

- Keep the source database read-only and the benchmark free of telemetry.
- PostgreSQL is the reference adapter, tested on 15 and 16.
- Install from a clone of this repository.
- Make the result useful at the scales people actually ask about, without turning the sandbox into a production replica.

## Next, if contributors pick them up

These are missing on purpose today. None of them block a useful PostgreSQL run.

- Parameterized SQL. Workloads use literals. `database.sample_common_values` is the opt-in that puts common values into the synthetic data so those literals can match.
- Objects the workload calls but the sandbox does not recreate: views, functions, and extensions that are not in the sandbox image. Today those queries fail as `execution_failure`.
- Partitioned tables. They are loaded as one table, so partition pruning is not what the measurement shows.
- Adapters besides PostgreSQL. The contract is in [docs/contributing/adapters.md](docs/contributing/adapters.md). An adapter is not "we can connect."

## Not the project

- Replaying production traffic or copying production rows.
- Applying index or schema changes to the source database.
- A hosted DBScale service, an account, or a telemetry backend.
- Replacing a full-application load test. DBScale runs the SQL you give it, in a sandbox, at several sizes.

Issues that match this list are welcome. A pull request does not need prior permission. See [CONTRIBUTING.md](CONTRIBUTING.md).
