-- Small deterministic e-commerce schema with intentional bottlenecks:
--   * orders.user_id has NO index            -> missing index / sequential scan
--   * orders.created_at has NO index         -> large sort when ordering recent orders
--   * events has only a primary key          -> full scans + aggregation over everything
--   * order_items has a composite primary key -> exercises mixed-radix key generation
-- The same schema is used by examples/postgres/init/01-ecommerce.sql.

CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'cancelled');

CREATE TABLE users (
    id          serial PRIMARY KEY,
    email       varchar(255) NOT NULL UNIQUE,
    full_name   text NOT NULL,
    country     varchar(2) NOT NULL,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE categories (
    id    serial PRIMARY KEY,
    name  text NOT NULL,
    parent_id integer REFERENCES categories(id)
);

CREATE TABLE products (
    id           serial PRIMARY KEY,
    sku          varchar(32) NOT NULL UNIQUE,
    name         text NOT NULL,
    category_id  integer NOT NULL REFERENCES categories(id),
    price        numeric(10, 2) NOT NULL,
    attributes   jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_products_category ON products (category_id);

CREATE TABLE orders (
    id          bigserial PRIMARY KEY,
    user_id     integer NOT NULL REFERENCES users(id),
    status      order_status NOT NULL DEFAULT 'pending',
    total       numeric(12, 2) NOT NULL,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- intentionally no index on (user_id) or (created_at)

CREATE TABLE order_items (
    order_id    bigint NOT NULL REFERENCES orders(id),
    product_id  integer NOT NULL REFERENCES products(id),
    quantity    smallint NOT NULL,
    unit_price  numeric(10, 2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
CREATE INDEX idx_order_items_product ON order_items (product_id);

CREATE TABLE events (
    id           bigserial PRIMARY KEY,
    user_id      integer REFERENCES users(id),
    kind         varchar(32) NOT NULL,
    payload      jsonb,
    occurred_at  timestamptz NOT NULL DEFAULT now()
);

-- Seed enough rows for pg_stats to be meaningful.
INSERT INTO users (email, full_name, country, is_active, created_at)
SELECT 'user' || i || '@example.com', 'User ' || i,
       (ARRAY['US','DE','GB','FR','BR'])[1 + (i % 5)],
       i % 10 <> 0,
       now() - (i || ' minutes')::interval
FROM generate_series(1, 2000) AS i;

INSERT INTO categories (name, parent_id)
SELECT 'Category ' || i, CASE WHEN i > 5 THEN 1 + (i % 5) END FROM generate_series(1, 20) AS i;

INSERT INTO products (sku, name, category_id, price, attributes, created_at)
SELECT 'SKU-' || i, 'Product ' || i, 1 + (i % 20), (i % 500) + 0.99,
       jsonb_build_object('color', (ARRAY['red','blue','green'])[1 + (i % 3)]),
       now() - (i || ' hours')::interval
FROM generate_series(1, 500) AS i;

INSERT INTO orders (user_id, status, total, note, created_at)
SELECT 1 + (i % 2000),
       (ARRAY['pending','paid','shipped','cancelled']::order_status[])[1 + (i % 4)],
       (i % 1000) + 0.5,
       CASE WHEN i % 7 = 0 THEN 'gift' END,
       now() - (i || ' minutes')::interval
FROM generate_series(1, 10000) AS i;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT 1 + ((i - 1) % 10000), 1 + ((i - 1) / 10000) % 500, 1 + (i % 3), (i % 100) + 0.99
FROM generate_series(1, 25000) AS i;

INSERT INTO events (user_id, kind, payload, occurred_at)
SELECT CASE WHEN i % 20 = 0 THEN NULL ELSE 1 + (i % 2000) END,
       (ARRAY['page_view','click','purchase','login'])[1 + (i % 4)],
       jsonb_build_object('n', i),
       now() - (i || ' seconds')::interval
FROM generate_series(1, 20000) AS i;

ANALYZE;
