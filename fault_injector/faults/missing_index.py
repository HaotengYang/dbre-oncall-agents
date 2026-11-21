"""Missing-index faults."""

import random

from fault_injector.background import BackgroundClients
from fault_injector.baseline import BASELINE_INDEXES, drop_index_sql
from fault_injector.faults.base import Fault


class DroppedIndex(Fault):
    """Drops the index behind the workload's "recent orders for a customer"
    query. A quarter of all traffic turns from an index lookup into a scan of
    the whole orders table. No extra traffic is added: the application's own
    queries become the problem, as when an index is removed by a bad release.
    """

    fault_id = "missing_index.dropped_index"
    root_cause = "missing_index_slow_query"
    summary = "Drop the (customer_id, created_at) index on orders."
    dropped_index_name = "idx_orders_customer_created"
    planned_changes = ("drop index idx_orders_customer_created on shop.orders",)

    def start(self, lab_access):
        definition = next(
            definition for definition in BASELINE_INDEXES
            if definition.name == self.dropped_index_name
        )
        with lab_access.control_connection.cursor() as cursor:
            cursor.execute(drop_index_sql(definition))

    def stop(self, lab_access):
        # The index is recreated by the baseline restore that follows every fault.
        pass


# Filters and sorts on columns that have never had an index. Every execution
# scans all of order_items and sorts the matches.
UNINDEXED_REPORT_SQL = (
    "SELECT order_items.order_id, order_items.quantity, order_items.unit_price_cents "
    "FROM shop.order_items "
    "WHERE order_items.product_id = %s AND order_items.quantity >= %s "
    "ORDER BY order_items.unit_price_cents DESC LIMIT 50"
)
PRODUCT_COUNT = 1_000


def run_unindexed_report_repeatedly(connection, stop_requested, pause_seconds, random_seed):
    random_source = random.Random(random_seed)
    while not stop_requested.is_set():
        with connection.cursor() as cursor:
            cursor.execute(
                UNINDEXED_REPORT_SQL,
                (random_source.randint(1, PRODUCT_COUNT), random_source.randint(1, 3)),
            )
            cursor.fetchall()
        stop_requested.wait(pause_seconds)


class UnindexedQuery(Fault):
    """A new kind of query starts arriving that filters and sorts on columns
    with no index. The schema is not changed at all: nothing was dropped, the
    table simply never had an index for this access pattern. This is the shape
    of a release that ships a new report or search feature without one.
    """

    fault_id = "missing_index.unindexed_query"
    root_cause = "missing_index_slow_query"
    summary = "Run a new query that scans order_items; the schema is unchanged."
    client_count = 6
    pause_between_queries_seconds = 0.2
    planned_changes = (
        "run a new report query from 6 application connections that filters "
        "order_items by product_id and quantity; no schema change",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        client_numbers = iter(range(self.client_count))

        def client_body(connection, stop_requested):
            run_unindexed_report_repeatedly(
                connection, stop_requested, self.pause_between_queries_seconds,
                random_seed=next(client_numbers),
            )

        self._clients = BackgroundClients(
            lab_access.open_app_connection, client_body,
            client_limit=self.client_count, seconds_between_clients=0.1,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None
