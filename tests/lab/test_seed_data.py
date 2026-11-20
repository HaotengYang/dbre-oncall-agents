"""Completion criterion 3: seed row counts equal the expected values exactly."""

import pytest

from shop_workload.query_mix import DEFAULT_SEED_SIZES
from tests.lab.conftest import fetch_all, fetch_value

pytestmark = pytest.mark.lab

EXPECTED_ROW_COUNTS = {
    "customers": 10_000,
    "products": 1_000,
    "orders": 200_000,
    "order_items": 400_000,
}


@pytest.mark.parametrize("table_name, expected_count", sorted(EXPECTED_ROW_COUNTS.items()))
def test_row_count_is_exact(connect_as, table_name, expected_count):
    connection = connect_as("shop_diag", database="shop")

    assert fetch_value(connection, f"SELECT COUNT(*) FROM {table_name}") == expected_count


def test_workload_seed_sizes_match_the_database(connect_as):
    connection = connect_as("shop_diag", database="shop")

    assert fetch_value(connection, "SELECT MAX(id) FROM customers") == DEFAULT_SEED_SIZES.customers
    assert fetch_value(connection, "SELECT MAX(id) FROM products") == DEFAULT_SEED_SIZES.products
    assert fetch_value(connection, "SELECT MAX(id) FROM orders") == DEFAULT_SEED_SIZES.orders


def test_every_customer_has_exactly_twenty_orders(connect_as):
    connection = connect_as("shop_diag", database="shop")

    rows = fetch_all(
        connection,
        "SELECT MIN(order_count), MAX(order_count), COUNT(*) FROM "
        "(SELECT customer_id, COUNT(*) AS order_count FROM orders GROUP BY customer_id) "
        "AS orders_per_customer",
    )

    assert rows == ((20, 20, 10_000),)


def test_every_order_has_exactly_two_items(connect_as):
    connection = connect_as("shop_diag", database="shop")

    rows = fetch_all(
        connection,
        "SELECT MIN(item_count), MAX(item_count), COUNT(*) FROM "
        "(SELECT order_id, COUNT(*) AS item_count FROM order_items GROUP BY order_id) "
        "AS items_per_order",
    )

    assert rows == ((2, 2, 200_000),)


def test_seed_values_are_deterministic(connect_as):
    """Spot values computed by hand from the formulas in 02-seed.sql."""
    connection = connect_as("shop_diag", database="shop")

    # Order 1: customer 1 + (7919 mod 10000) = 7920.
    assert fetch_value(connection, "SELECT customer_id FROM orders WHERE id = 1") == 7920
    # Item 1: product 1 + ((13 + 397) mod 1000) = 411, quantity 1 + (1 mod 3) = 2,
    # price 500 + ((411 * 37) mod 9500) = 500 + 5707 = 6207.
    assert fetch_all(
        connection,
        "SELECT order_id, product_id, quantity, unit_price_cents FROM order_items WHERE id = 1",
    ) == ((1, 411, 2, 6207),)
    # The product table must agree with the price copied into the item.
    assert fetch_value(connection, "SELECT price_cents FROM products WHERE id = 411") == 6207


def test_order_totals_equal_the_sum_of_their_items(connect_as):
    connection = connect_as("shop_diag", database="shop")

    mismatched_orders = fetch_value(
        connection,
        "SELECT COUNT(*) FROM orders JOIN "
        "(SELECT order_id, SUM(quantity * unit_price_cents) AS items_total_cents "
        " FROM order_items GROUP BY order_id) AS order_totals "
        "ON order_totals.order_id = orders.id "
        "WHERE orders.total_cents <> order_totals.items_total_cents",
    )

    assert mismatched_orders == 0
    assert fetch_value(connection, "SELECT COUNT(*) FROM orders WHERE total_cents = 0") == 0
