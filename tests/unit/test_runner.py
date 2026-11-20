"""A worker must survive any failure and keep working."""

import threading
import types

from prometheus_client import REGISTRY

from shop_workload.runner import run_worker
from shop_workload.settings import WorkloadSettings

SETTINGS = WorkloadSettings(
    mysql_host="unused",
    mysql_port=3306,
    mysql_database="shop",
    mysql_user="unused",
    mysql_password="unused",
    operations_per_second=2000.0,
    worker_count=1,
    random_seed=1,
    metrics_port=8000,
)


def total_operations(outcome):
    operation_names = [
        "order_by_id", "recent_orders_for_customer", "items_for_order",
        "product_by_id", "update_order_status",
    ]
    return sum(
        REGISTRY.get_sample_value(
            "workload_operations_total", {"operation": name, "outcome": outcome}
        ) or 0.0
        for name in operation_names
    )


class FakeCursor:
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exception_details):
        return False

    def execute(self, sql, parameters):
        self._connection.harness.record_execute(self._connection)

    def fetchall(self):
        return ()


class FakeConnection:
    def __init__(self, harness):
        self.harness = harness
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class WorkerHarness:
    """Fake database: fails chosen executes, stops the worker after a limit."""

    def __init__(self, failing_execute_numbers, failure, stop_after_executes):
        self.failing_execute_numbers = failing_execute_numbers
        self.failure = failure
        self.stop_after_executes = stop_after_executes
        self.stop_requested = threading.Event()
        self.connections = []
        self.execute_count = 0

    def open_connection(self, settings):
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection

    def record_execute(self, connection):
        self.execute_count += 1
        if self.execute_count >= self.stop_after_executes:
            self.stop_requested.set()
        if self.execute_count in self.failing_execute_numbers:
            raise self.failure


def run_until_stopped(harness):
    worker = threading.Thread(
        target=run_worker,
        args=(0, SETTINGS, harness.stop_requested, harness.open_connection),
    )
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "worker did not stop when asked"


def test_worker_survives_an_unexpected_exception_type():
    harness = WorkerHarness(
        failing_execute_numbers={3},
        failure=RuntimeError("not a database error"),
        stop_after_executes=10,
    )
    ok_before = total_operations("ok")
    errors_before = total_operations("error")

    run_until_stopped(harness)

    # It kept going after the failure instead of dying at execute 3.
    assert harness.execute_count == 10
    assert total_operations("error") - errors_before == 1
    assert total_operations("ok") - ok_before == 9


def test_worker_reconnects_after_a_failure_and_closes_the_broken_connection():
    harness = WorkerHarness(
        failing_execute_numbers={2, 5},
        failure=ConnectionResetError("socket closed"),
        stop_after_executes=8,
    )

    run_until_stopped(harness)

    assert len(harness.connections) == 3  # the first, plus one per failure
    assert all(connection.closed for connection in harness.connections)


def test_worker_keeps_retrying_when_the_database_refuses_connections(monkeypatch):
    monkeypatch.setattr("shop_workload.runner.RECONNECT_DELAY_SECONDS", 0.001)
    attempts = types.SimpleNamespace(count=0)
    stop_requested = threading.Event()

    def refuse_connection(settings):
        attempts.count += 1
        if attempts.count >= 5:
            stop_requested.set()
        raise OSError("too many connections")

    worker = threading.Thread(
        target=run_worker, args=(0, SETTINGS, stop_requested, refuse_connection)
    )
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert attempts.count == 5
