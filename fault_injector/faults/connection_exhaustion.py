"""Connection-exhaustion faults."""

import contextlib

import pymysql

from fault_injector.faults.base import Fault

TOO_MANY_CONNECTIONS_ERROR = 1040


class IdleFlood(Fault):
    """Opens idle application connections until the server refuses more, and
    holds them. Existing sessions keep working; anything that needs a new
    connection is turned away. This is the shape of a connection pool that
    leaks, or of too many application instances started at once.
    """

    fault_id = "connection_exhaustion.idle_flood"
    root_cause = "connection_exhaustion"
    summary = "Hold idle application connections until the server refuses new ones."
    # Far above the lab's max_connections. Only a guard against an endless
    # loop if the server never refuses.
    connection_safety_cap = 500
    planned_changes = (
        "open idle connections as shop_app until the server refuses more, and hold them",
    )

    def __init__(self):
        self._held_connections = []

    def start(self, lab_access):
        while len(self._held_connections) < self.connection_safety_cap:
            try:
                self._held_connections.append(lab_access.open_app_connection())
            except pymysql.err.OperationalError as connect_error:
                if connect_error.args[0] == TOO_MANY_CONNECTIONS_ERROR:
                    return
                raise
        raise RuntimeError(
            f"opened {self.connection_safety_cap} connections and the server still accepts more"
        )

    def stop(self, lab_access):
        for held_connection in self._held_connections:
            with contextlib.suppress(Exception):
                held_connection.close()
        self._held_connections = []
