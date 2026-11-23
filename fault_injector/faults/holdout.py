"""Scenarios for the holdout eval cases.

Designed by the explorer subagent without sight of the rule engine's code
(docs/proposals/holdout-cases.md), and implemented here as written there. The
owner chose four of the six proposed: H1, H4, H5, H6. Neither the rule engine
nor any model prompt may be adjusted to these scenarios (CLAUDE.md).
"""

import contextlib
import random

import pymysql

from fault_injector.background import BackgroundClients
from fault_injector.faults.base import Fault

CLIENT_SAFETY_CAP = 500
PRODUCT_COUNT = 1_000


def call_slow_statement_until_stopped(connection, stop_requested):
    """H1: a primary-key lookup that waits four seconds. Slow for a reason no
    index can fix (a stand-in for a slow stored function or external call)."""
    random_source = random.Random()
    while not stop_requested.is_set():
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name, price_cents, SLEEP(4) FROM shop.products WHERE id = %s",
                    (random_source.randint(1, PRODUCT_COUNT),),
                )
                cursor.fetchall()
        except pymysql.MySQLError:
            stop_requested.wait(0.5)


class ActiveSlowCalls(Fault):
    """H1. Every connection is taken by sessions actively running a statement
    that is slow for a reason no index can fix. A new application connection
    arrives every 0.4 s, each looping on a four-second primary-key lookup."""

    fault_id = "connection_exhaustion.active_slow_calls"
    root_cause = "connection_exhaustion"
    summary = "Connections fill with sessions running a slow call that no index can speed up."
    seconds_between_clients = 0.4
    planned_changes = (
        "start a new application connection every 0.4 s, each repeatedly running a "
        "4-second primary-key lookup on shop.products, until the server refuses more",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        self._clients = BackgroundClients(
            lab_access.open_app_connection, call_slow_statement_until_stopped,
            client_limit=CLIENT_SAFETY_CAP, seconds_between_clients=self.seconds_between_clients,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None


def update_customers_by_unindexed_name(connection, stop_requested):
    """H4: an update filtered on a column with no index locks every row it
    scans, so concurrent copies queue behind each other. The name rarely
    matches; every scanned row is locked regardless. Data never changes
    (the column is assigned to itself)."""
    random_source = random.Random()
    while not stop_requested.is_set():
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE shop.customers SET country_code = country_code WHERE full_name = %s",
                    (f"Customer {random_source.randint(1, 20_000)}",),
                )
            stop_requested.wait(0.3)
            connection.commit()
        except pymysql.MySQLError:
            with contextlib.suppress(Exception):
                connection.rollback()
            stop_requested.wait(0.5)
    with contextlib.suppress(Exception):
        connection.rollback()


class UnindexedUpdateBlocksWriters(Fault):
    """H4. Six writers update customers by an unindexed column, holding each
    transaction for 0.3 s. Each update locks the whole table, so the writers
    wait on each other. Root cause: the missing index on customers(full_name);
    the lock waits are its symptom (with the index they disappear)."""

    fault_id = "cascade.unindexed_update_blocks_writers"
    root_cause = "missing_index_slow_query"
    expected_symptoms = ("lock_wait",)
    summary = "Updates on an unindexed column lock all of customers and queue behind each other."
    client_count = 6
    planned_changes = (
        "run 6 application connections that repeatedly update shop.customers filtered on "
        "the unindexed column full_name, each holding its transaction 0.3 s; no data change",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        self._clients = BackgroundClients(
            lambda: lab_access.open_app_connection(autocommit=False),
            update_customers_by_unindexed_name,
            client_limit=self.client_count, seconds_between_clients=0.1,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None


def update_a_product_price_until_stopped(connection, stop_requested):
    random_source = random.Random()
    while not stop_requested.is_set():
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE shop.products SET price_cents = price_cents WHERE id = %s",
                    (random_source.randint(1, PRODUCT_COUNT),),
                )
        except pymysql.MySQLError:
            stop_requested.wait(0.5)


class ProductsRowLock(Fault):
    """H5. A stalled transaction holds row locks on all of products, and four
    back-office writers are stuck behind it. The normal workload never writes
    products and its reads are not blocked, so only the database's own lock
    evidence shows the fault."""

    fault_id = "lock_wait.products_row_lock"
    root_cause = "lock_wait"
    summary = "An idle transaction locks all products; four price editors wait behind it."
    first_locked_product_id = 1
    last_locked_product_id = 1_000
    writer_count = 4
    planned_changes = (
        "open a transaction as shop_app holding row locks on shop.products ids 1 to 1000",
        "run 4 application connections that repeatedly update a product's price to itself",
    )

    def __init__(self):
        self._holder_connection = None
        self._clients = None

    def start(self, lab_access):
        self._holder_connection = lab_access.open_app_connection(autocommit=False)
        with self._holder_connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM shop.products WHERE id BETWEEN %s AND %s FOR UPDATE",
                (self.first_locked_product_id, self.last_locked_product_id),
            )
            cursor.fetchall()
        self._clients = BackgroundClients(
            lab_access.open_app_connection, update_a_product_price_until_stopped,
            client_limit=self.writer_count, seconds_between_clients=0.1,
        )
        self._clients.start()

    def stop(self, lab_access):
        # Release the locks first so the blocked writers return.
        if self._holder_connection is not None:
            with contextlib.suppress(Exception):
                self._holder_connection.rollback()
            with contextlib.suppress(Exception):
                self._holder_connection.close()
            self._holder_connection = None
        if self._clients is not None:
            self._clients.stop()
            self._clients = None


def hold_idle(connection, stop_requested):
    stop_requested.wait()


class SteadyIdlePool(Fault):
    """H6. Twenty idle application connections appear within two seconds and
    stay. The count steps up once and holds at about half the limit; nothing
    is refused. A bigger connection pool after scaling out, not a fault."""

    fault_id = "healthy.steady_idle_pool"
    root_cause = "healthy"
    summary = "Twenty idle connections appear at once and stay; nothing is refused."
    connection_count = 20
    planned_changes = ("open 20 idle application connections within 2 s and hold them",)

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        self._clients = BackgroundClients(
            lab_access.open_app_connection, hold_idle,
            client_limit=self.connection_count, seconds_between_clients=0.1,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None
