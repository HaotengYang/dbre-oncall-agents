"""The statements the synthetic workload runs, and how one is chosen.

The mix only reads and updates. It never inserts or deletes, so table row
counts stay equal to the seed sizes for the whole life of the lab. Every
statement uses an index in the baseline schema; a healthy lab has no slow
statements of its own.
"""

from dataclasses import dataclass
from typing import Callable

ORDER_STATUSES = ("pending", "paid", "shipped", "delivered", "cancelled")


@dataclass(frozen=True)
class SeedSizes:
    """Row counts created by lab/mysql/init/02-seed.sql."""

    customers: int
    products: int
    orders: int


DEFAULT_SEED_SIZES = SeedSizes(customers=10_000, products=1_000, orders=200_000)


@dataclass(frozen=True)
class QueryTemplate:
    name: str
    weight: int
    is_write: bool
    sql: str
    parameter_names: tuple
    draw_parameters: Callable


@dataclass(frozen=True)
class Operation:
    """One statement ready to execute: SQL text plus separate parameters."""

    name: str
    is_write: bool
    sql: str
    parameter_names: tuple
    parameters: tuple


def build_query_mix(seed_sizes):
    def random_customer_id(random_source):
        return random_source.randint(1, seed_sizes.customers)

    def random_order_id(random_source):
        return random_source.randint(1, seed_sizes.orders)

    def random_product_id(random_source):
        return random_source.randint(1, seed_sizes.products)

    return (
        QueryTemplate(
            name="order_by_id",
            weight=30,
            is_write=False,
            sql=(
                "SELECT id, customer_id, status, total_cents, created_at "
                "FROM orders WHERE id = %s"
            ),
            parameter_names=("order_id",),
            draw_parameters=lambda random_source: (random_order_id(random_source),),
        ),
        QueryTemplate(
            name="recent_orders_for_customer",
            weight=25,
            is_write=False,
            sql=(
                "SELECT id, status, total_cents, created_at "
                "FROM orders WHERE customer_id = %s "
                "ORDER BY created_at DESC LIMIT 10"
            ),
            parameter_names=("customer_id",),
            draw_parameters=lambda random_source: (random_customer_id(random_source),),
        ),
        QueryTemplate(
            name="items_for_order",
            weight=20,
            is_write=False,
            sql=(
                "SELECT order_items.product_id, products.name, "
                "order_items.quantity, order_items.unit_price_cents "
                "FROM order_items "
                "JOIN products ON products.id = order_items.product_id "
                "WHERE order_items.order_id = %s"
            ),
            parameter_names=("order_id",),
            draw_parameters=lambda random_source: (random_order_id(random_source),),
        ),
        QueryTemplate(
            name="product_by_id",
            weight=10,
            is_write=False,
            sql="SELECT id, name, price_cents FROM products WHERE id = %s",
            parameter_names=("product_id",),
            draw_parameters=lambda random_source: (random_product_id(random_source),),
        ),
        QueryTemplate(
            name="update_order_status",
            weight=15,
            is_write=True,
            sql="UPDATE orders SET status = %s WHERE id = %s",
            parameter_names=("status", "order_id"),
            draw_parameters=lambda random_source: (
                random_source.choice(ORDER_STATUSES),
                random_order_id(random_source),
            ),
        ),
    )


def choose_operation(query_mix, random_source):
    """Pick a template by weight and fill in its parameters."""
    weights = [template.weight for template in query_mix]
    template = random_source.choices(query_mix, weights=weights, k=1)[0]
    return Operation(
        name=template.name,
        is_write=template.is_write,
        sql=template.sql,
        parameter_names=template.parameter_names,
        parameters=template.draw_parameters(random_source),
    )
