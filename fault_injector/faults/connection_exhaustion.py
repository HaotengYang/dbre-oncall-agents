"""Connection-exhaustion faults."""

import contextlib
import threading

import pymysql

from fault_injector.background import BackgroundClients
from fault_injector.faults.base import Fault

TOO_MANY_CONNECTIONS_ERROR = 1040


class IdleFlood(Fault):
    """Opens idle application connections until the server refuses more, and
    keeps it full. Existing sessions keep working; anything that needs a new
    connection is turned away. This is the shape of too many application
    instances started at once.

    Staying full matters. Other clients come and go (the metrics exporter
    reconnects, for example), and each time one leaves a slot opens. Measured
    before the top-up existed: a diagnosis that should have been locked out
    got in through such a slot. A background thread takes freed slots again
    within half a second.
    """

    fault_id = "connection_exhaustion.idle_flood"
    root_cause = "connection_exhaustion"
    summary = "Hold idle application connections so the server refuses new ones."
    # Far above the lab's max_connections. Only a guard against an endless
    # loop if the server never refuses.
    connection_safety_cap = 500
    top_up_interval_seconds = 0.5
    planned_changes = (
        "open idle connections as shop_app until the server refuses more, hold them, "
        "and take any slot that frees up",
    )

    def __init__(self):
        self._held_connections = []
        self._stop_topping_up = threading.Event()
        self._top_up_thread = None

    def _fill(self, lab_access):
        """Opens connections until the server refuses. Returns how many it added."""
        added = 0
        while len(self._held_connections) < self.connection_safety_cap:
            try:
                self._held_connections.append(lab_access.open_app_connection())
                added += 1
            except pymysql.err.OperationalError as connect_error:
                if connect_error.args[0] == TOO_MANY_CONNECTIONS_ERROR:
                    return added
                raise
        raise RuntimeError(
            f"opened {self.connection_safety_cap} connections and the server still accepts more"
        )

    def _keep_full(self, lab_access):
        while not self._stop_topping_up.wait(self.top_up_interval_seconds):
            with contextlib.suppress(Exception):
                self._fill(lab_access)

    def start(self, lab_access):
        self._fill(lab_access)
        self._top_up_thread = threading.Thread(
            target=self._keep_full, args=(lab_access,), daemon=True
        )
        self._top_up_thread.start()

    def stop(self, lab_access):
        # Stop topping up before closing, or freed slots would be retaken.
        self._stop_topping_up.set()
        if self._top_up_thread is not None:
            self._top_up_thread.join(timeout=20)
            self._top_up_thread = None
        for held_connection in self._held_connections:
            with contextlib.suppress(Exception):
                held_connection.close()
        self._held_connections = []


def hold_connection_idle(connection, stop_requested):
    stop_requested.wait()


class GradualLeak(Fault):
    """Application connections are opened one after another and never
    returned, about one a second, until the server refuses more. Unlike the
    flood, the connection count climbs steadily for about a minute before
    anything fails. This is the shape of a connection pool that leaks.
    """

    fault_id = "connection_exhaustion.gradual_leak"
    root_cause = "connection_exhaustion"
    summary = "Leak one idle application connection a second until the server is full."
    seconds_between_connections = 1.0
    connection_safety_cap = 500
    planned_changes = (
        "open one idle connection as shop_app every second and never close it, "
        "until the server refuses more",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        self._clients = BackgroundClients(
            lab_access.open_app_connection, hold_connection_idle,
            client_limit=self.connection_safety_cap,
            seconds_between_clients=self.seconds_between_connections,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None
