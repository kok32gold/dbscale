# Quickstart

## 1. Start a database you are allowed to read

```bash
docker compose -f examples/postgres/docker-compose.yml up -d --wait
```

That is a local demo. To use your own database instead, export
`DATABASE_URL` and point `database.connection` at it. DBScale opens that
connection read-only.

## 2. Run the smallest example

```bash
cd examples/simple-postgres
dbscale run
```

You should see a read-only inspect, a disposable sandbox, two scale targets,
and a JSON file `dbscale-results.json`.

## 3. Read the result

The terminal lists each query, latency at each scale, findings, and
recommendations. `dbscale report dbscale-results.json` prints the same report
later, with no database.

## 4. Try a planted bottleneck

```bash
cd ../missing-index
dbscale run
```

`missing-index` is a lookup on an unindexed column. Expect a sequential-scan
finding and an `ADD_INDEX` recommendation. The recommendation is a proposal.
It is not applied to your database.

Stop the demo when you are done:

```bash
docker compose -f examples/postgres/docker-compose.yml down -v
```
