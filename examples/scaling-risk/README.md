# Scaling risk

Two queries whose work grows with the table:

- `large-sort` orders `events` by `occurred_at` with no supporting index
- `aggregation` groups `orders` by `user_id`

```bash
docker compose -f ../postgres/docker-compose.yml up -d --wait
dbscale run
```

Read the scaling line under each query. An exponent near 1 means latency
tracks data size. The rules advisor may suggest an ordering index or a
pre-aggregation. Those are proposals attached to findings, not measured fixes.

100x on this demo is still a laptop-sized database. The shape of the curve
is the point, not the absolute milliseconds.
