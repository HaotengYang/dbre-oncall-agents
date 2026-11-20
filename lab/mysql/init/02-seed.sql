-- Deterministic seed data. No RAND(), no NOW(): every value is computed from
-- the row number, so two machines produce byte-identical tables and eval
-- results are comparable.
--
-- Row counts: customers 10,000; products 1,000; orders 200,000;
-- order_items 400,000 (exactly two per order). Keep in sync with
-- DEFAULT_SEED_SIZES in lab/workload/shop_workload/query_mix.py; a lab test
-- compares the two.

USE shop;

SET SESSION cte_max_recursion_depth = 1000000;

INSERT INTO customers (id, email, full_name, country_code, created_at)
WITH RECURSIVE sequence_numbers (sequence_number) AS (
  SELECT 1 UNION ALL SELECT sequence_number + 1 FROM sequence_numbers WHERE sequence_number < 10000
)
SELECT
  sequence_number,
  CONCAT('customer', sequence_number, '@example.test'),
  CONCAT('Customer ', sequence_number),
  ELT(1 + MOD(sequence_number, 5), 'US', 'GB', 'DE', 'FR', 'JP'),
  TIMESTAMP('2025-01-01 00:00:00') + INTERVAL MOD(sequence_number * 7919, 31536000) SECOND
FROM sequence_numbers;

INSERT INTO products (id, sku, name, category, price_cents)
WITH RECURSIVE sequence_numbers (sequence_number) AS (
  SELECT 1 UNION ALL SELECT sequence_number + 1 FROM sequence_numbers WHERE sequence_number < 1000
)
SELECT
  sequence_number,
  CONCAT('SKU-', LPAD(sequence_number, 6, '0')),
  CONCAT('Product ', sequence_number),
  ELT(1 + MOD(sequence_number, 8),
      'dresses', 'tops', 'shoes', 'bags', 'jewelry', 'home', 'beauty', 'sports'),
  500 + MOD(sequence_number * 37, 9500)
FROM sequence_numbers;

-- customer_id cycles through all customers evenly: 7919 is prime and shares
-- no factor with 10000, so each customer gets exactly 20 orders.
INSERT INTO orders (id, customer_id, status, total_cents, created_at, updated_at)
WITH RECURSIVE sequence_numbers (sequence_number) AS (
  SELECT 1 UNION ALL SELECT sequence_number + 1 FROM sequence_numbers WHERE sequence_number < 200000
)
SELECT
  sequence_number,
  1 + MOD(sequence_number * 7919, 10000),
  ELT(1 + MOD(sequence_number * 31, 5), 'pending', 'paid', 'shipped', 'delivered', 'cancelled'),
  0,
  TIMESTAMP('2026-01-01 00:00:00') + INTERVAL MOD(sequence_number * 104729, 25920000) SECOND,
  TIMESTAMP('2026-01-01 00:00:00') + INTERVAL MOD(sequence_number * 104729, 25920000) SECOND
FROM sequence_numbers;

-- Two items per order: item rows 2k-1 and 2k belong to order k.
INSERT INTO order_items (id, order_id, product_id, quantity, unit_price_cents)
WITH RECURSIVE sequence_numbers (sequence_number) AS (
  SELECT 1 UNION ALL SELECT sequence_number + 1 FROM sequence_numbers WHERE sequence_number < 400000
)
SELECT
  sequence_number,
  (sequence_number + 1) DIV 2,
  1 + MOD(sequence_number * 13 + 397, 1000),
  1 + MOD(sequence_number, 3),
  500 + MOD((1 + MOD(sequence_number * 13 + 397, 1000)) * 37, 9500)
FROM sequence_numbers;

-- Order totals follow from the items. updated_at is assigned to itself so the
-- ON UPDATE clause does not stamp the current time into seed data.
UPDATE orders
JOIN (
  SELECT order_id, SUM(quantity * unit_price_cents) AS items_total_cents
  FROM order_items
  GROUP BY order_id
) AS order_totals ON order_totals.order_id = orders.id
SET orders.total_cents = order_totals.items_total_cents,
    orders.updated_at = orders.updated_at;

ANALYZE TABLE customers, products, orders, order_items;
