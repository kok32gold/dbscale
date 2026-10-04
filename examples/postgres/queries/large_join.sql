-- Join + filter: hash join across users and orders, filtered on a low-cardinality column.
SELECT o.id, o.total, u.email
FROM orders o
JOIN users u ON u.id = o.user_id
WHERE u.country = 'US' AND o.status = 'paid'
LIMIT 500;
