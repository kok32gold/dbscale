-- Healthy: unique index on products.sku makes this a point lookup.
SELECT id, name, price FROM products WHERE sku = 'sku-7';
