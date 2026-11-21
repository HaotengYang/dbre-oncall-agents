"""Lock-wait faults."""

import contextlib
import threading

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


class BlockedSchemaChange(Fault):
    """A schema change gets stuck behind an open transaction and then blocks
    everyone else.

    One application session reads a row of orders inside a transaction and
    never ends it. That holds a shared metadata lock on the table. An "add
    index" then waits for an exclusive metadata lock, and while it waits,
    every new statement on orders queues behind it. Reads and writes that hit
    no row lock at all are stuck in "Waiting for table metadata lock". This is
    the classic way a harmless-looking online schema change takes a table down.
    """

    fault_id = "lock_wait.blocked_schema_change"
    root_cause = "lock_wait"
    summary = "An add-index waits behind an open transaction and blocks all traffic on orders."
    pending_index_name = "idx_fault_orders_status"
    ddl_lock_wait_seconds = 3600
    planned_changes = (
        "open a transaction as shop_app that has read shop.orders and stays open",
        "start CREATE INDEX idx_fault_orders_status on shop.orders, which waits for a metadata lock",
    )

    def __init__(self):
        self._holder_connection = None
        self._ddl_connection = None
        self._ddl_session_id = None
        self._ddl_thread = None

    def start(self, lab_access):
        self._holder_connection = lab_access.open_app_connection(autocommit=False)
        with self._holder_connection.cursor() as cursor:
            cursor.execute("SELECT id FROM shop.orders WHERE id = 1")
            cursor.fetchall()

        self._ddl_connection = lab_access.open_schema_connection()
        with self._ddl_connection.cursor() as cursor:
            cursor.execute("SELECT CONNECTION_ID()")
            self._ddl_session_id = cursor.fetchone()[0]
            cursor.execute(f"SET SESSION lock_wait_timeout = {self.ddl_lock_wait_seconds}")
        self._ddl_thread = threading.Thread(target=self._run_blocked_ddl, daemon=True)
        self._ddl_thread.start()

    def _run_blocked_ddl(self):
        # Blocks until the holder's transaction ends or the statement is killed.
        with contextlib.suppress(Exception):
            with self._ddl_connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE INDEX {self.pending_index_name} ON shop.orders (status) "
                    "ALGORITHM = INPLACE LOCK = NONE"
                )

    def stop(self, lab_access):
        # Kill the waiting schema change first. If the holder were released
        # first, the index would actually be built.
        if self._ddl_session_id is not None:
            with contextlib.suppress(Exception):
                with lab_access.control_connection.cursor() as cursor:
                    cursor.execute(f"KILL {int(self._ddl_session_id)}")
        if self._ddl_thread is not None:
            self._ddl_thread.join(timeout=20)
        for connection in (self._ddl_connection, self._holder_connection):
            if connection is None:
                continue
            with contextlib.suppress(Exception):
                connection.rollback()
            with contextlib.suppress(Exception):
                connection.close()
        self._holder_connection = self._ddl_connection = None
        self._ddl_session_id = self._ddl_thread = None
        # If the index was built after all (for example the injector was
        # killed and the holder went away first), the baseline restore that
        # follows removes it: its name carries the injector's prefix.
