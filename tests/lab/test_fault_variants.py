"""The second variant of each fault class and the two cascading faults: each
must really produce the state its description claims, and nothing else."""

import time

import pytest

from tests.lab.conftest import fetch_all, fetch_value
from tests.lab.fault_helpers import (
    clear_and_wait_for_baseline,
    read_injection_events,
    schema_snapshot,
    start_fault,
    stop_fault,
    wait_until,
)

pytestmark = pytest.mark.lab

SESSIONS = (
    "SELECT COUNT(*) FROM performance_schema.processlist "
    "WHERE USER NOT IN ('event_scheduler', 'system user')"
)
IDLE_APP_SESSIONS = (
    "SELECT COUNT(*) FROM performance_schema.processlist "
    "WHERE USER = 'shop_app' AND COMMAND = 'Sleep'"
)
ROW_LOCK_WAITS = "SELECT COUNT(*) FROM performance_schema.data_lock_waits"
METADATA_LOCK_WAITERS = (
    "SELECT COUNT(*) FROM performance_schema.processlist "
    "WHERE STATE = 'Waiting for table metadata lock'"
)
REPORT_QUERIES_RUNNING = (
    "SELECT COUNT(*) FROM performance_schema.processlist "
    "WHERE USER = 'shop_app' AND COMMAND = 'Query' AND INFO LIKE '%%order_items%%product_id%%'"
)
REPORT_DIGEST_FILTER = (
    "SCHEMA_NAME IS NULL AND DIGEST_TEXT LIKE 'SELECT%%FROM `shop` . `order_items` WHERE%%'"
)
MAX_CONNECTIONS = 60
NEARLY_FULL = 0.9 * MAX_CONNECTIONS


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def last_started_event():
    return [event for event in read_injection_events() if event["event"] == "started"][-1]


def test_unindexed_query_scans_the_table_without_any_schema_change(observer_connection):
    schema_before = schema_snapshot(observer_connection)

    injector = start_fault("missing_index.unindexed_query")
    try:
        wait_until(lambda: fetch_value(observer_connection, REPORT_QUERIES_RUNNING) >= 1,
                   timeout_seconds=20, interval_seconds=0.2)
        time.sleep(10)
        schema_during = schema_snapshot(observer_connection)
        report_digests = fetch_all(
            observer_connection,
            "SELECT COUNT_STAR, SUM_ROWS_EXAMINED, SUM_NO_INDEX_USED "
            "FROM performance_schema.events_statements_summary_by_digest "
            f"WHERE {REPORT_DIGEST_FILTER} AND COUNT_STAR > 0",
        )
        with observer_connection.cursor() as cursor:
            cursor.execute(
                "EXPLAIN SELECT order_id FROM shop.order_items "
                "WHERE product_id = 411 AND quantity >= 2 ORDER BY unit_price_cents DESC LIMIT 50"
            )
            plan = dict(zip([column[0] for column in cursor.description], cursor.fetchone()))
    finally:
        stop_fault(injector)

    # The defining property of this variant: nothing in the schema differs.
    assert schema_during == schema_before
    assert len(report_digests) == 1, report_digests
    executions, rows_examined, executions_without_index = (int(v) for v in report_digests[0])
    assert executions >= 10
    assert executions_without_index == executions
    assert rows_examined / executions == pytest.approx(400_000, rel=0.05)  # all of order_items
    assert (plan["type"], plan["key"]) == ("ALL", None)
    assert last_started_event()["root_cause"] == "missing_index_slow_query"


def test_blocked_schema_change_queues_everything_behind_a_metadata_lock(
    observer_connection, prometheus_query
):
    failed_operations = 'sum(workload_operations_total{outcome="error"}) or vector(0)'
    failures_before = prometheus_query(failed_operations)[0][1]

    injector = start_fault("lock_wait.blocked_schema_change")
    try:
        wait_until(lambda: fetch_value(observer_connection, METADATA_LOCK_WAITERS) >= 3,
                   timeout_seconds=20, interval_seconds=0.5)
        waiting_statements = fetch_all(
            observer_connection,
            "SELECT USER, INFO FROM performance_schema.processlist "
            "WHERE STATE = 'Waiting for table metadata lock'",
        )
        row_lock_waits_during = fetch_value(observer_connection, ROW_LOCK_WAITS)
        index_exists_during = fetch_value(
            observer_connection,
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = 'shop' AND INDEX_NAME = 'idx_fault_orders_status'",
        )
        time.sleep(15)  # long enough for blocked workload statements to time out
        failures_during = prometheus_query(failed_operations)[0][1]
    finally:
        stop_fault(injector)

    waiting_users = {user for user, _ in waiting_statements}
    # The stuck schema change and the application statements queued behind it.
    assert any(user == "shop_fault" and "CREATE INDEX" in statement
               for user, statement in waiting_statements)
    assert "shop_app" in waiting_users
    # Reads are stuck too, which a row lock could never cause.
    assert any(user == "shop_app" and statement.lstrip().upper().startswith("SELECT")
               for user, statement in waiting_statements)
    assert row_lock_waits_during == 0  # this is a metadata lock, not a row lock
    assert index_exists_during == 0  # the index is waiting, not built
    assert failures_during - failures_before >= 4

    # The waiting schema change was cancelled, not let through.
    assert fetch_value(
        observer_connection,
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = 'shop' AND INDEX_NAME = 'idx_fault_orders_status'",
    ) == 0
    assert fetch_value(observer_connection, METADATA_LOCK_WAITERS) == 0


def test_gradual_leak_climbs_steadily_before_it_fills(observer_connection):
    sessions_before = fetch_value(observer_connection, SESSIONS)

    injector = start_fault("connection_exhaustion.gradual_leak")
    try:
        time.sleep(8)
        sessions_early = fetch_value(observer_connection, SESSIONS)
        time.sleep(12)
        sessions_later = fetch_value(observer_connection, SESSIONS)
        seconds_until_full = 20 + wait_until(
            lambda: fetch_value(observer_connection, SESSIONS) >= NEARLY_FULL,
            timeout_seconds=70,
        )
        idle_app_sessions_when_full = fetch_value(observer_connection, IDLE_APP_SESSIONS)
    finally:
        stop_fault(injector)

    # A ramp, not a step: clearly above baseline early on, clearly not yet
    # full, and still climbing at about one connection a second.
    assert sessions_before + 4 <= sessions_early <= sessions_before + 14
    assert sessions_early + 8 <= sessions_later <= sessions_early + 18
    assert sessions_later < NEARLY_FULL
    assert 30 <= seconds_until_full <= 90
    assert idle_app_sessions_when_full >= NEARLY_FULL - 10  # leaked connections sit idle


def test_lock_wait_cascade_fills_connections_with_sessions_blocked_on_one_transaction(
    observer_connection,
):
    injector = start_fault("cascade.lock_wait_exhausts_connections")
    try:
        wait_until(lambda: fetch_value(observer_connection, SESSIONS) >= NEARLY_FULL,
                   timeout_seconds=60)
        # A session is connected a moment before its update starts waiting,
        # so the lock waits are polled for rather than read once.
        wait_until(lambda: fetch_value(observer_connection, ROW_LOCK_WAITS) >= 40,
                   timeout_seconds=20, interval_seconds=0.5)
        row_lock_waits = fetch_value(observer_connection, ROW_LOCK_WAITS)
        blockers = fetch_all(
            observer_connection,
            "SELECT DISTINCT blocking_trx.trx_rows_locked, session.USER, session.COMMAND "
            "FROM performance_schema.data_lock_waits AS lock_wait "
            "JOIN information_schema.INNODB_TRX AS blocking_trx "
            "  ON blocking_trx.trx_id = lock_wait.BLOCKING_ENGINE_TRANSACTION_ID "
            "JOIN performance_schema.processlist AS session "
            "  ON session.ID = blocking_trx.trx_mysql_thread_id",
        )
        idle_app_sessions = fetch_value(observer_connection, IDLE_APP_SESSIONS)
    finally:
        stop_fault(injector)

    # The symptom: connections are used up. The cause: nearly all of those
    # sessions are waiting on row locks held by one idle transaction.
    assert row_lock_waits >= 40
    assert len(blockers) == 1, blockers
    rows_locked, blocker_user, blocker_command = blockers[0]
    assert rows_locked >= 50_000 and (blocker_user, blocker_command) == ("shop_app", "Sleep")
    assert idle_app_sessions <= 10  # unlike an idle flood, the sessions are busy waiting
    started = last_started_event()
    assert started["root_cause"] == "lock_wait"
    assert started["expected_symptoms"] == ["connection_exhaustion"]


def test_slow_query_cascade_fills_connections_with_running_scans(observer_connection):
    schema_before = schema_snapshot(observer_connection)

    injector = start_fault("cascade.slow_query_exhausts_connections")
    try:
        wait_until(lambda: fetch_value(observer_connection, SESSIONS) >= NEARLY_FULL,
                   timeout_seconds=60)
        report_queries_running = fetch_value(observer_connection, REPORT_QUERIES_RUNNING)
        row_lock_waits = fetch_value(observer_connection, ROW_LOCK_WAITS)
        idle_app_sessions = fetch_value(observer_connection, IDLE_APP_SESSIONS)
        schema_during = schema_snapshot(observer_connection)
    finally:
        stop_fault(injector)

    # The symptom: connections are used up. The cause: they are all running
    # the same unindexed scan. No locks are involved and the schema is intact.
    assert report_queries_running >= 30
    assert row_lock_waits == 0
    assert idle_app_sessions <= 15
    assert schema_during == schema_before
    started = last_started_event()
    assert started["root_cause"] == "missing_index_slow_query"
    assert started["expected_symptoms"] == ["connection_exhaustion"]
