# Security policy

## Reporting a vulnerability

Use GitHub private vulnerability reporting on this repository (Security → Report
a vulnerability). Do not open a public issue for an unfixed vulnerability.

If private reporting is unavailable, open a public issue that describes the
bug class without a working exploit, credentials, or production data, and say
that details are available privately.

## What to include

- DBScale version (`dbscale --version`)
- What an attacker, or an accidental misuse, can do
- A reproduction that uses a synthetic schema and throwaway credentials
- Whether the issue is in the source-database read path, the sandbox, result
  files, or the optional AI advisor

## What not to include

Do not send or publish:

- database credentials or connection strings with real passwords
- production schemas that contain sensitive names or data
- private SQL from a production workload
- API keys, tokens, or private keys
- customer data of any kind

Replace those with a minimal synthetic example. DBScale's own demo database
(`examples/postgres`) is a safe starting point.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x   | yes, latest release only |
| < 0.1   | no |

The project is pre-1.0. Only the latest 0.1 release receives security fixes.
See [docs/versioning.md](docs/versioning.md).

## Scope

In scope: the DBScale code in this repository, including how it connects to a
database, what it reads from the source, what it writes into result files, and
how optional AI requests are built.

Out of scope: vulnerabilities in PostgreSQL, Docker, or an LLM provider you
configure yourself. A bug that lets DBScale write to the source database, copy
row data without `sample_common_values`, or leak a secret into a result file
or an error message is in scope.
