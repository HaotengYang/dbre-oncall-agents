"""Scenarios replaying two public incidents (evals/incident-replays/).

Each reproduces the pattern of the incident in the lab, not the incident
itself. What differs from the original is written in the case files and the
README. Neither the rule engine nor any prompt may be tuned to them.
"""

import contextlib
import random

import pymysql

from fault_injector import container_limits
from fault_injector.background import BackgroundClients
from fault_injector.baseline import BASELINE_INDEXES, drop_index_sql
from fault_injector.faults.base import Fault

CLIENT_SAFETY_CAP = 500
CUSTOMER_COUNT = 10_000
ORDER_COUNT = 200_000

CUSTOMER_ORDERS_SQL = (
    "SELECT id, status, total_cents, created_at FROM shop.orders WHERE customer_id = %s "
    "ORDER BY created_at DESC LIMIT 10"
)


def request_customer_orders_until_stopped(connection, stop_requested):
    random_source = random.Random()
    while not stop_requested.is_set():
        try:
            with connection.cursor() as cursor:
                cursor.execute(CUSTOMER_ORDERS_SQL, (random_source.randint(1, CUSTOMER_COUNT),))
                cursor.fetchall()
        except pymysql.MySQLError:
            stop_requested.wait(0.5)
        stop_requested.wait(0.05)


class GitHub2022QueryDegradesAtPeak(Fault):
    """GitHub, 2022-03-16 pattern: at peak traffic one query family performs
    badly, connections through the proxy run out, and writes fail.

    In the lab: the index behind the customer-orders query is dropped (the
    query degrades to a full scan), and peak traffic for that query arrives
    as a new application connection every 0.3 s, each repeating it, until the
    server refuses more connections. The normal workload's writes then fail
    whenever they need a new connection.
    """

    fault_id = "incident.github_2022_03_query_degrades_at_peak"
    root_cause = "missing_index_slow_query"
    expected_symptoms = ("connection_exhaustion",)
    summary = "A query degrades at peak traffic; connections run out; writes fail."
    dropped_index_name = "idx_orders_customer_created"
    seconds_between_clients = 0.3
    planned_changes = (
        "drop index idx_orders_customer_created on shop.orders",
        "start a new application connection every 0.3 s, each repeating the customer-orders "
        "query, until the server refuses more",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        definition = next(d for d in BASELINE_INDEXES if d.name == self.dropped_index_name)
        with lab_access.control_connection.cursor() as cursor:
            cursor.execute(drop_index_sql(definition))
        self._clients = BackgroundClients(
            lab_access.open_app_connection, request_customer_orders_until_stopped,
            client_limit=CLIENT_SAFETY_CAP, seconds_between_clients=self.seconds_between_clients,
        )
        self._clients.start()

    def stop(self, lab_access):
        if self._clients is not None:
            self._clients.stop()
            self._clients = None
        # The index comes back with the baseline restore that follows.


def compensating_client(connection, stop_requested):
    """An application client whose requests are ordinary indexed reads. When a
    request is slow it does not wait: it keeps its connection busy and the
    application opens another one to compensate (see BackgroundClients)."""
    random_source = random.Random()
    while not stop_requested.is_set():
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, customer_id, status, total_cents, created_at FROM shop.orders "
                    "WHERE id = %s", (random_source.randint(1, ORDER_COUNT),))
                cursor.fetchall()
                cursor.execute(CUSTOMER_ORDERS_SQL, (random_source.randint(1, CUSTOMER_COUNT),))
                cursor.fetchall()
        except pymysql.MySQLError:
            stop_requested.wait(0.5)


class Honeycomb2018DatabaseSlowsDown(Fault):
    """Honeycomb, 2018-05-03 pattern: the database as a whole becomes slow, the
    application's connection pool saturates, clients open new connections to
    compensate, and the server ends at error 1040.

    The original cause was degraded RDS hardware, which a lab cannot reproduce.
    The closest lab equivalent: the MySQL container's CPU limit is cut from 1.0
    to 0.05, so every statement, however well indexed, runs slowly. On top,
    application clients that open a new connection every 0.25 s (compensating
    for slow ones) until the server refuses more.
    """

    fault_id = "incident.honeycomb_2018_05_database_slows_down"
    root_cause = "connection_exhaustion"
    summary = "The whole database slows down; clients open ever more connections until error 1040."
    degraded_cpus = "0.05"
    seconds_between_clients = 0.25
    planned_changes = (
        "cut the MySQL container's CPU limit from 1.0 to 0.05",
        "start a new application connection every 0.25 s running ordinary indexed reads, "
        "until the server refuses more",
    )

    def __init__(self):
        self._clients = None

    def start(self, lab_access):
        container_limits.set_cpu_limit(self.degraded_cpus)
        self._clients = BackgroundClients(
            lab_access.open_app_connection, compensating_client,
            client_limit=CLIENT_SAFETY_CAP, seconds_between_clients=self.seconds_between_clients,
        )
        self._clients.start()

    def stop(self, lab_access):
        try:
            container_limits.restore_cpu_limit()
        finally:
            if self._clients is not None:
                self._clients.stop()
                self._clients = None
