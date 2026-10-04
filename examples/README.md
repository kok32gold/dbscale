# Examples

All of these use one local demo database. The password `src` exists only
inside that container, bound to `127.0.0.1`.

```bash
docker compose -f examples/postgres/docker-compose.yml up -d --wait
```

| Directory | What it shows |
| --- | --- |
| [simple-postgres](simple-postgres/) | Smallest successful run. An indexed lookup at 1x and 10x. |
| [missing-index](missing-index/) | A lookup that becomes a sequential scan. |
| [scaling-risk](scaling-risk/) | Sort and aggregation whose cost grows with the data. |
| [multiple-workloads](multiple-workloads/) | Several queries in one experiment, including a healthy control. |
| [ai-analysis](ai-analysis/) | The same benchmark with AI off, then an optional interpretation. |
| [postgres](postgres/) | The demo schema and the full planted-bottleneck suite. |

```bash
cd examples/simple-postgres
dbscale run
```

Tear down:

```bash
docker compose -f examples/postgres/docker-compose.yml down -v
```
