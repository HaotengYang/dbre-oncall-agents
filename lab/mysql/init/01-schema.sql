-- Baseline schema for the lab shop database.
-- Every statement in the synthetic workload is served by an index defined
-- here, so a freshly started lab has no slow statements of its own.

USE shop;

CREATE TABLE customers (
  id            INT UNSIGNED NOT NULL,
  email         VARCHAR(255) NOT NULL,
  full_name     VARCHAR(100) NOT NULL,
  country_code  CHAR(2)      NOT NULL,
  created_at    DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_customers_email (email)
) ENGINE = InnoDB;

CREATE TABLE products (
  id           INT UNSIGNED NOT NULL,
  sku          VARCHAR(32)  NOT NULL,
  name         VARCHAR(100) NOT NULL,
  category     VARCHAR(50)  NOT NULL,
  price_cents  INT UNSIGNED NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_products_sku (sku)
) ENGINE = InnoDB;

CREATE TABLE orders (
  id           BIGINT UNSIGNED NOT NULL,
  customer_id  INT UNSIGNED    NOT NULL,
  status       VARCHAR(20)     NOT NULL,
  total_cents  INT UNSIGNED    NOT NULL,
  created_at   DATETIME        NOT NULL,
  updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                               ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_orders_customer_created (customer_id, created_at)
) ENGINE = InnoDB;

CREATE TABLE order_items (
  id                BIGINT UNSIGNED NOT NULL,
  order_id          BIGINT UNSIGNED NOT NULL,
  product_id        INT UNSIGNED    NOT NULL,
  quantity          INT UNSIGNED    NOT NULL,
  unit_price_cents  INT UNSIGNED    NOT NULL,
  PRIMARY KEY (id),
  KEY idx_order_items_order (order_id)
) ENGINE = InnoDB;
