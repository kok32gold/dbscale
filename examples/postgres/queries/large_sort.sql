-- Bottleneck: no index on events.occurred_at -> full scan + sort of the whole table.
SELECT id, user_id, kind, occurred_at
FROM events
ORDER BY occurred_at DESC
LIMIT 100;
