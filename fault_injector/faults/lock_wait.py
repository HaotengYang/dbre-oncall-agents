"""Lock-wait faults."""

import contextlib

from fault_injector.faults.base import Fault


class LongTransaction(Fault):
    """One application session opens a transaction, locks a quarter of the
    orders rows and then does nothing. The workload's updates to those rows
    wait for the lock and fail when their lock wait times out. This is the
    shape of a forgotten transaction or a batch job that stalled mid-way.
    """

    fault_id = "lock_wait.long_transaction"
    root_cause = "lock_wait"
    summary = "Hold row locks on a quarter of orders in an idle open transaction."
    first_locked_order_id = 1
    last_locked_order_id = 50_000
    planned_changes = (
        "open a transaction as shop_app holding row locks on shop.orders ids 1 to 50000",
    )

    def __init__(self):
        self._holder_connection = None

    def start(self, lab_access):
        self._holder_connection = lab_access.open_app_connection(autocommit=False)
        with self._holder_connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM shop.orders WHERE id BETWEEN %s AND %s FOR UPDATE",
                (self.first_locked_order_id, self.last_locked_order_id),
            )
            cursor.fetchall()

    def stop(self, lab_access):
        if self._holder_connection is None:
            return
        with contextlib.suppress(Exception):
            self._holder_connection.rollback()
        with contextlib.suppress(Exception):
            self._holder_connection.close()
        self._holder_connection = None
