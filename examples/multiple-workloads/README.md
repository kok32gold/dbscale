# Multiple workloads

One experiment, five statements: a missing index, a healthy primary-key
lookup, a large sort, a join, and an aggregation.

```bash
docker compose -f ../postgres/docker-compose.yml up -d --wait
dbscale run
```

Each query gets its own findings. A healthy query should not inherit another
query's recommendation. `indexed-lookup` is the control.

This is the same schema as [postgres](../postgres/). That directory also has
the SQL files and a sixth query (`sku-lookup`).
