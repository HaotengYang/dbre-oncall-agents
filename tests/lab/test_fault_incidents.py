"""The incident-replay scenarios produce their pattern and leave the lab as it was."""

import time

import pymysql
import pytest

from fault_injector import container_limits
from tests.lab.conftest import fetch_all, fetch_value
from tests.lab.fault_helpers import (
    clear_and_wait_for_baseline,
    row_counts,
    run_injector,
    schema_snapshot,
    start_fault,
    stop_fault,
    wait_until,
)

pytestmark = pytest.mark.lab
SESSIONS = ("SELECT COUNT(*) FROM performance_schema.processlist "
            "WHERE USER NOT IN ('event_scheduler', 'system user')")


def refused(lab_env):
    try:
        pymysql.connect(host="127.0.0.1", port=int(lab_env.get("MYSQL_HOST_PORT", "13306")),
                        user="shop_app", password=lab_env["APP_DB_PASSWORD"], connect_timeout=5).close()
        return False
    except pymysql.err.OperationalError as error:
        return error.args[0] == 1040


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after(connect_as):
    clear_and_wait_for_baseline()
    observer = connect_as("shop_diag")
    before = (schema_snapshot(observer), row_counts(observer))
    yield
    clear_and_wait_for_baseline()
    observer = connect_as("shop_diag")
    assert (schema_snapshot(observer), row_counts(observer)) == before
    assert container_limits.current_nano_cpus() == container_limits.BASELINE_NANO_CPUS


def test_github_pattern_scans_and_fills_connections(observer_connection, lab_env):
    injector = start_fault("incident.github_2022_03_query_degrades_at_peak")
    try:
        wait_until(lambda: refused(lab_env), timeout_seconds=60)
        scanning = fetch_value(observer_connection,
                               "SELECT COUNT(*) FROM performance_schema.processlist "
                               "WHERE COMMAND = 'Query' AND INFO LIKE '%%customer_id =%%'")
        index_names = {row[2] for row in fetch_all(observer_connection, "SHOW INDEX FROM shop.orders")}
    finally:
        stop_fault(injector)

    assert "idx_orders_customer_created" not in index_names
    assert scanning >= 10


def test_honeycomb_pattern_slows_everything_and_ends_at_1040(observer_connection, lab_env):
    injector = start_fault("incident.honeycomb_2018_05_database_slows_down")
    try:
        assert container_limits.current_nano_cpus() == 50_000_000
        wait_until(lambda: refused(lab_env), timeout_seconds=90)
        lock_waits = fetch_value(observer_connection,
                                 "SELECT COUNT(*) FROM performance_schema.data_lock_waits")
        index_names = {row[2] for row in fetch_all(observer_connection, "SHOW INDEX FROM shop.orders")}
    finally:
        stop_fault(injector)

    assert lock_waits == 0
    assert "idx_orders_customer_created" in index_names  # nothing is missing; it is all just slow
    assert container_limits.current_nano_cpus() == container_limits.BASELINE_NANO_CPUS


def test_clear_restores_the_cpu_limit_after_a_killed_injector():
    import signal

    injector = start_fault("incident.honeycomb_2018_05_database_slows_down")
    injector.send_signal(signal.SIGKILL)
    injector.wait(timeout=10)
    assert container_limits.current_nano_cpus() == 50_000_000

    cleared = run_injector("clear")

    assert cleared.returncode == 0
    assert "restored the MySQL container's CPU limit" in cleared.stdout
    assert container_limits.current_nano_cpus() == container_limits.BASELINE_NANO_CPUS
