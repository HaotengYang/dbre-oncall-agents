"""Scenarios that are not faults.

They make the lab look busier or slower in some respect while nothing is
wrong. A diagnosis system that reports a fault for them has raised a false
alarm. Their root-cause label is "healthy".
"""

import random

from fault_injector.background import BackgroundClients
from fault_injector.faults.base import Fault

HEALTHY_LABEL = "healthy"
CUSTOMER_COUNT = 10_000
ORDER_COUNT = 200_000
PRODUCT_COUNT = 1_000

# The same indexed statements the normal workload runs.
INDEXED_READS = (
    ("SELECT id, customer_id, status, total_cents, created_at FROM shop.orders WHERE id = %s",
     ORDER_COUNT),
    ("SELECT id, status, total_cents, created_at FROM shop.orders WHERE customer_id = %s "
     "ORDER BY created_at DESC LIMIT 10", CUSTOMER_COUNT),
    ("SELECT id, name, price_cents FROM shop.products WHERE id = %s", PRODUCT_COUNT),
)


def run_indexed_reads(connection, stop_requested, pause_seconds=0.2):
    random_source = random.Random()
    while not stop_requested.is_set():
        statement, highest_id = random_source.choice(INDEXED_READS)
        with connection.cursor() as cursor:
            cursor.execute(statement, (random_source.randint(1, highest_id),))
            cursor.fetchall()
        stop_requested.wait(pause_seconds)


class HighLoad(Fault):
    """Three times the normal traffic, all of it indexed and fast. More
    sessions, more queries per second, nothing slow and nothing blocked: a
    busy day, not an incident.
    """

    fault_id = "healthy.high_load"
    root_cause = HEALTHY_LABEL
    summary = "Triple the normal traffic with indexed, fast statements only."
    client_count = 8
    planned_changes = (
        "run about 40 extra indexed reads per second from 8 application connections",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        self._clients = BackgroundClients(
            lab_access.open_app_connection, run_indexed_reads,
            client_limit=self.client_count, seconds_between_clients=0.1,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None


# A whole-table aggregate. It reads every row by design; no index would help.
CATEGORY_REVENUE_REPORT_SQL = (
    "SELECT products.category, COUNT(*) AS items_sold, "
    "SUM(order_items.quantity * order_items.unit_price_cents) AS revenue_cents "
    "FROM shop.order_items JOIN shop.products ON products.id = order_items.product_id "
    "GROUP BY products.category"
)


def run_report_periodically(connection, stop_requested, seconds_between_reports=10):
    while not stop_requested.is_set():
        with connection.cursor() as cursor:
            cursor.execute(CATEGORY_REVENUE_REPORT_SQL)
            cursor.fetchall()
        stop_requested.wait(seconds_between_reports)


class SlowReport(Fault):
    """One reporting query that reads the whole order_items table, once every
    ten seconds. It is slow, it always has been, and it harms nobody: it is a
    whole-table aggregate that no index could speed up, and it runs rarely.
    """

    fault_id = "healthy.slow_report"
    root_cause = HEALTHY_LABEL
    summary = "A rare, inherently slow reporting query that harms nothing."
    planned_changes = (
        "run one whole-table revenue report every 10 s from one application connection",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        self._clients = BackgroundClients(
            lab_access.open_app_connection, run_report_periodically,
            client_limit=1, seconds_between_clients=0.1,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None
