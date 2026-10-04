-- Bottleneck: aggregates every order on each execution.
SELECT user_id, count(*) AS orders, sum(total) AS revenue
FROM orders
GROUP BY user_id
ORDER BY revenue DESC
LIMIT 10;
