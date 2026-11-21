"""Cascading faults: one root cause, with connection exhaustion as its symptom.

In both, the server ends up with every connection slot taken. A diagnosis that
stops there and answers "connection exhaustion" has named the symptom. The
root cause is what made the connections pile up.
"""

import contextlib
import random

import pymysql

from fault_injector.background import BackgroundClients
from fault_injector.faults.base import Fault
from fault_injector.faults.lock_wait import LongTransaction
from fault_injector.faults.missing_index import PRODUCT_COUNT, UNINDEXED_REPORT_SQL

CLIENT_SAFETY_CAP = 500


def update_a_locked_order_until_stopped(connection, stop_requested):
    """One impatient application instance: its update is stuck on a row lock,
    and it keeps retrying on the same connection."""
    locked_order_id = random.randint(
        LongTransaction.first_locked_order_id, LongTransaction.last_locked_order_id
    )
    while not stop_requested.is_set():
        try:
            with connection.cursor() as cursor:
                # Assigns the column to itself: the row's data never changes,
                # but the statement still needs the row lock.
                cursor.execute(
                    "UPDATE shop.orders SET status = status WHERE id = %s", (locked_order_id,)
                )
        except pymysql.MySQLError:
            if stop_requested.is_set():
                return
            stop_requested.wait(0.5)


class LockWaitExhaustsConnections(Fault):
    """A long transaction holds row locks. Application instances whose updates
    are stuck behind it do not give up; more keep arriving, each on a new
    connection, each stuck in turn, until the server has no connections left.

    Root cause: the lock wait. Symptom: connection exhaustion.
    """

    fault_id = "cascade.lock_wait_exhausts_connections"
    root_cause = "lock_wait"
    expected_symptoms = ("connection_exhaustion",)
    summary = "Sessions pile up behind a long transaction until connections run out."
    seconds_between_clients = 0.4
    planned_changes = (
        "open a transaction as shop_app holding row locks on shop.orders ids 1 to 50000",
        "start a new application connection every 0.4 s, each blocked updating a locked "
        "row, until the server refuses more",
    )

    def __init__(self):
        self._lock_holder = LongTransaction()
        self._clients = None

    def start(self, lab_access):
        self._lock_holder.start(lab_access)
        self._clients = BackgroundClients(
            lab_access.open_app_connection, update_a_locked_order_until_stopped,
            client_limit=CLIENT_SAFETY_CAP,
            seconds_between_clients=self.seconds_between_clients,
        )
        self._clients.start()

    def stop(self, lab_access):
        # Release the row locks first so the blocked updates return and their
        # threads can end; then stop and close the clients.
        self._lock_holder.stop(lab_access)
        if self._clients is not None:
            self._clients.stop()
            self._clients = None


def run_unindexed_report_until_stopped(connection, stop_requested):
    random_source = random.Random()
    while not stop_requested.is_set():
        with contextlib.suppress(pymysql.MySQLError):
            with connection.cursor() as cursor:
                cursor.execute(
                    UNINDEXED_REPORT_SQL,
                    (random_source.randint(1, PRODUCT_COUNT), random_source.randint(1, 3)),
                )
                cursor.fetchall()


class SlowQueryExhaustsConnections(Fault):
    """A query with no usable index is slow. Callers keep arriving faster than
    it finishes, each on a new connection, so running copies accumulate, slow
    each other down further, and take every connection.

    Root cause: the missing index. Symptom: connection exhaustion. The schema
    is not changed.
    """

    fault_id = "cascade.slow_query_exhausts_connections"
    root_cause = "missing_index_slow_query"
    expected_symptoms = ("connection_exhaustion",)
    summary = "Copies of an unindexed query accumulate until connections run out."
    seconds_between_clients = 0.4
    planned_changes = (
        "start a new application connection every 0.4 s, each running a report query "
        "that scans order_items in a loop, until the server refuses more; no schema change",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        self._clients = BackgroundClients(
            lab_access.open_app_connection, run_unindexed_report_until_stopped,
            client_limit=CLIENT_SAFETY_CAP,
            seconds_between_clients=self.seconds_between_clients,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None
