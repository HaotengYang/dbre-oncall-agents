"""The holdout scenarios produce the states their proposal describes
(docs/proposals/holdout-cases.md, completion criteria 3 to 14)."""

import time

import pymysql
import pytest

from tests.lab.conftest import fetch_all, fetch_value
from tests.lab.fault_helpers import (
    clear_and_wait_for_baseline,
    schema_snapshot,
    start_fault,
    stop_fault,
    wait_until,
)

pytestmark = pytest.mark.lab

SESSIONS = ("SELECT COUNT(*) FROM performance_schema.processlist "
            "WHERE USER NOT IN ('event_scheduler', 'system user')")
FAILED_UPDATES = ('sum(workload_operations_total{operation="update_order_status",'
                  'outcome="error"}) or vector(0)')


def lock_waits_on(connection, table_name):
    return fetch_value(
        connection,
        "SELECT COUNT(*) FROM performance_schema.data_lock_waits AS lock_wait "
        "JOIN performance_schema.data_locks AS requested "
        "  ON requested.ENGINE_LOCK_ID = lock_wait.REQUESTING_ENGINE_LOCK_ID "
        "WHERE requested.OBJECT_NAME = %s",
        (table_name,),
    )


def try_app_connection(lab_env):
    try:
        return pymysql.connect(host="127.0.0.1", port=int(lab_env.get("MYSQL_HOST_PORT", "13306")),
                               user="shop_app", password=lab_env["APP_DB_PASSWORD"], connect_timeout=5)
    except pymysql.err.OperationalError as refused:
        return refused.args[0]


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def test_h1_active_slow_calls_fill_connections_without_locks(observer_connection, lab_env):
    injector = start_fault("connection_exhaustion.active_slow_calls")
    try:
        wait_until(lambda: try_app_connection(lab_env) == 1040, timeout_seconds=40)
        sleeping_calls = fetch_value(
            observer_connection,
            "SELECT COUNT(*) FROM performance_schema.processlist "
            "WHERE COMMAND = 'Query' AND INFO LIKE '%%SLEEP(4)%%'",
        )
        lock_waits = fetch_value(observer_connection,
                                 "SELECT COUNT(*) FROM performance_schema.data_lock_waits")
        clear_requested_at = time.monotonic()
    finally:
        stop_fault(injector)

    assert sleeping_calls >= 40
    assert lock_waits == 0
    wait_until(lambda: fetch_value(observer_connection, SESSIONS) < 10, timeout_seconds=15)
    assert time.monotonic() - clear_requested_at < 20


def test_h4_unindexed_update_queues_writers_and_an_index_removes_the_queue(
    observer_connection, connect_as
):
    """Criteria 11 and 12: the waits are there, and an index makes them go away.
    Criterion 12 is what settles the ground truth (owner, 2026-11-21)."""
    schema_before = schema_snapshot(observer_connection)
    injector = start_fault("cascade.unindexed_update_blocks_writers")
    try:
        time.sleep(5)
        samples_without_index = []
        for _ in range(20):
            samples_without_index.append(lock_waits_on(observer_connection, "customers"))
            time.sleep(0.5)

        fault_connection = connect_as("shop_fault")
        with fault_connection.cursor() as cursor:
            cursor.execute("CREATE INDEX idx_fault_customers_full_name "
                           "ON shop.customers (full_name) ALGORITHM = INPLACE LOCK = NONE")
        time.sleep(3)
        samples_with_index = []
        for _ in range(20):
            samples_with_index.append(lock_waits_on(observer_connection, "customers"))
            time.sleep(0.5)
    finally:
        stop_fault(injector)

    assert sum(1 for waits in samples_without_index if waits > 0) >= 15, samples_without_index
    assert samples_with_index == [0] * 20, samples_with_index
    # The temporary index carried the injector's prefix and was removed on stop.
    assert schema_snapshot(observer_connection) == schema_before


def test_h5_products_lock_blocks_writers_but_not_the_workload(observer_connection,
                                                             prometheus_query):
    failed_before = prometheus_query(FAILED_UPDATES)[0][1]
    injector = start_fault("lock_wait.products_row_lock")
    try:
        wait_until(lambda: lock_waits_on(observer_connection, "products") > 0,
                   timeout_seconds=20, interval_seconds=0.5)
        waits_on_orders = lock_waits_on(observer_connection, "orders")
        time.sleep(15)
        failed_during = prometheus_query(FAILED_UPDATES)[0][1]
    finally:
        stop_fault(injector)

    assert waits_on_orders == 0
    assert failed_during - failed_before == 0  # the workload sees nothing


def test_h6_steady_idle_pool_holds_flat_and_accepts_new_connections(prometheus_query, lab_env):
    connections = "mysql_global_status_threads_connected"
    injector = start_fault("healthy.steady_idle_pool")
    try:
        time.sleep(10)  # one scrape after the step
        level_after_step = prometheus_query(connections)[0][1]
        readings = []
        for _ in range(10):
            time.sleep(5)
            readings.append(prometheus_query(connections)[0][1])
        accepted = try_app_connection(lab_env)
    finally:
        stop_fault(injector)

    assert level_after_step >= 22
    assert all(abs(reading - level_after_step) <= 2 for reading in readings), readings
    assert not isinstance(accepted, int)
    accepted.close()
