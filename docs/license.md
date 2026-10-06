# License

DBScale is licensed under the [Apache License 2.0](https://github.com/kok32gold/dbscale/blob/main/LICENSE).

You may use, modify, and redistribute this software, including in commercial
products and private forks, under those terms. There is no fee to the authors,
no non-commercial clause, and no ban on competing work. You must keep the
license and attribution notices. The license does not grant trademark rights.

This page is an explanation, not a replacement for the license. If they
differ, the `LICENSE` file wins.

## Why Apache-2.0

MIT and Apache-2.0 both allow commercial use, modification, and
redistribution. Apache-2.0 is the better fit here for two clauses:

- **Patent grant.** Downstream users and contributors get a license to
  patents that contributors hold in the work they submitted. A tool that
  emits index DDL and query advice will be embedded in other products. The
  grant is the reason not to stop at MIT.
- **Contributions.** A contribution you intentionally submit is licensed
  under Apache-2.0 unless you say otherwise. The project does not require a
  separate contributor agreement.

GPL-family licenses were not used. They would require derivative works of
DBScale itself to be published under the same terms, which is more than this
project asks of someone building a product on top of it.

## Contributor implications

Opening a pull request against this repository licenses that contribution
under Apache-2.0. Do not submit code you cannot license that way.

## Dependencies

DBScale's own source is Apache-2.0. Dependencies keep their licenses. The one
to know about is psycopg, which is LGPL-3.0. See [dependencies.md](dependencies.md).
