# PostgreSQL example

The shared demo database, plus a full experiment with every planted bottleneck.
Shorter lessons live next to it: [simple-postgres](../simple-postgres/),
[missing-index](../missing-index/), [scaling-risk](../scaling-risk/),
[multiple-workloads](../multiple-workloads/), [ai-analysis](../ai-analysis/).

A small e-commerce database with deliberately planted bottlenecks, and a DBScale
experiment that finds them. The password `src` is only for this local container.

```bash
docker compose -f examples/postgres/docker-compose.yml up -d --wait   # demo source database on 127.0.0.1:55432
cd examples/postgres
dbscale inspect              # what DBScale sees (schema + statistics only)
dbscale run                  # 1x → 10x → 100x, ~20s on a laptop
dbscale report dbscale-results.json
```

Planted problems (see `init/01-ecommerce.sql`):

| Query | Problem | Expected finding |
|---|---|---|
| `missing-index` | `orders.user_id` has no index | sequential scan → `ADD_INDEX` |
| `large-sort` | `events.occurred_at` has no index | large sort → ordering index |
| `large-join` | join on an unindexed column, filtered by `country`/`status` | expensive join |
| `aggregation` | `GROUP BY` over the whole `events` table | aggregation bottleneck at 100x |
| `indexed-lookup`, `sku-lookup` | nothing wrong | control queries that stay flat |

`sample_common_values: true` is enabled in `dbscale.yaml` so that literal predicates such as
`country = 'US'` match the synthetic data. It reads the most common values of low-cardinality
columns from PostgreSQL statistics; leave it off for databases where that is not acceptable.

To try AI interpretation of the results without re-running the benchmark:

```bash
OPENAI_API_KEY=... dbscale explain dbscale-results.json
# or a local model:
dbscale explain dbscale-results.json --provider ollama --model llama3.1
```

Tear down: `docker compose -f examples/postgres/docker-compose.yml down -v`.
