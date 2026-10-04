# Simple PostgreSQL

The shortest path: one indexed lookup, two scale targets, no AI.

```bash
docker compose -f ../postgres/docker-compose.yml up -d --wait
dbscale run
```

`indexed-lookup` reads `users` by primary key. Latency should stay flat.
Findings, if any, should not include a sequential scan of `users`.

The source database is the demo on `127.0.0.1:55432`. DBScale creates a
second, disposable PostgreSQL container for the benchmark and removes it
when the run finishes.
