-- Bottleneck: orders.user_id has no index -> sequential scan that grows with the table.
SELECT id, status, total, created_at
FROM orders
WHERE user_id = 42
ORDER BY created_at DESC
LIMIT 20;
