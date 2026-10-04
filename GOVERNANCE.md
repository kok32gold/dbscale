# Governance

DBScale is maintained as a normal open-source project. There is no committee,
no contributor ladder, and no approval required before you open a pull request.

## Maintainers

The people with write access to the repository review pull requests and publish
releases. The list is whoever GitHub shows as a maintainer of this repository.
That set can change. The project does not depend on one person remaining available:
the build, tests, and documentation all live in this repository.

## How a change gets in

1. Open a pull request. Bug fixes and features do not need prior permission.
2. A maintainer reviews it. Review looks at correctness, tests, the source-database
   privacy rules, and whether the change stays in the right layer.
3. CI must pass. It does not use private secrets.
4. A maintainer merges it.

Disagreement is resolved in the pull request. If maintainers disagree and cannot
settle it there, they say so in the thread and pick the smaller change that keeps
the project usable. That is the whole process.

## Releases

See [docs/versioning.md](docs/versioning.md). Any maintainer can cut a release
from `main` once CI is green and `CHANGELOG.md` matches the tag.

## Conduct

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Report a conduct problem by opening a
GitHub issue labeled `conduct`, or by using GitHub private vulnerability
reporting if the report contains personal information. Do not include credentials,
production schemas, or private SQL.
