# Missing index

One query, three scales. `orders.user_id` is not indexed.

```bash
docker compose -f ../postgres/docker-compose.yml up -d --wait
dbscale run
```

Expect a sequential-scan finding at the larger scales and an `ADD_INDEX`
recommendation. The SQL in that recommendation is a proposal. DBScale does
not create the index in the source database.

Compare with [simple-postgres](../simple-postgres/), which looks up a primary
key and should stay flat.
