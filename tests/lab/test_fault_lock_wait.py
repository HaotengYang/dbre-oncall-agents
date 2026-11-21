"""Completion criteria 10 to 12: the long-transaction fault really blocks the
workload's updates, does not grow connections, and stops cleanly."""

import time

import pytest

from tests.lab.conftest import fetch_all, fetch_value
from tests.lab.fault_helpers import run_injector, start_fault, stop_fault, wait_until

pytestmark = pytest.mark.lab

FAILED_UPDATES = (
    'sum(workload_operations_total{operation="update_order_status",outcome="error"}) or vector(0)'
)
APP_SESSIONS = "SELECT COUNT(*) FROM performance_schema.processlist WHERE USER = 'shop_app'"
LOCK_WAITS = "SELECT COUNT(*) FROM performance_schema.data_lock_waits"


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    assert run_injector("clear").returncode == 0
    yield
    assert run_injector("clear").returncode == 0


def blocking_transactions(connection):
    """Transactions that other sessions are waiting on: (rows locked, user, state)."""
    return fetch_all(
        connection,
        "SELECT DISTINCT blocking_trx.trx_rows_locked, session.USER, session.COMMAND "
        "FROM performance_schema.data_lock_waits AS lock_wait "
        "JOIN information_schema.INNODB_TRX AS blocking_trx "
        "  ON blocking_trx.trx_id = lock_wait.BLOCKING_ENGINE_TRANSACTION_ID "
        "JOIN performance_schema.processlist AS session "
        "  ON session.ID = blocking_trx.trx_mysql_thread_id",
    )


def test_long_transaction_blocks_updates_without_growing_connections(
    connect_as, prometheus_query
):
    diag_connection = connect_as("shop_diag")
    sessions_before = fetch_value(diag_connection, APP_SESSIONS)
    failed_updates_before = prometheus_query(FAILED_UPDATES)[0][1]
    assert fetch_value(diag_connection, LOCK_WAITS) == 0

    injector = start_fault("lock_wait.long_transaction")
    try:
        seconds_until_blocked = wait_until(
            lambda: fetch_value(diag_connection, LOCK_WAITS) > 0, timeout_seconds=20
        )
        blockers = blocking_transactions(diag_connection)
        peak_sessions = sessions_before
        observation_ends_at = time.monotonic() + 25
        while time.monotonic() < observation_ends_at:
            peak_sessions = max(peak_sessions, fetch_value(diag_connection, APP_SESSIONS))
            time.sleep(2)
        failed_updates_during = prometheus_query(FAILED_UPDATES)[0][1]

        # Criterion 12: within 15 s of "clear" no lock waits remain. The clock
        # starts before clear is requested, so a slow stop cannot hide.
        clear_requested_at = time.monotonic()
        cleared = run_injector("clear")
        wait_until(lambda: fetch_value(diag_connection, LOCK_WAITS) == 0,
                   timeout_seconds=15, interval_seconds=0.5)
        seconds_from_clear_to_no_lock_waits = time.monotonic() - clear_requested_at
    finally:
        if injector.poll() is None:
            stop_fault(injector)

    assert seconds_until_blocked <= 20
    # Exactly one blocker: an idle application session holding about 50,000 row
    # locks. That is the injected transaction and nothing else.
    assert len(blockers) == 1, blockers
    rows_locked, blocker_user, blocker_command = blockers[0]
    assert rows_locked >= 50_000
    assert (blocker_user, blocker_command) == ("shop_app", "Sleep")
    # The workload felt it: blocked updates timed out and were counted.
    assert failed_updates_during - failed_updates_before >= 1
    # A lock wait, not a cascade: only the one holding session was added.
    assert peak_sessions <= sessions_before + 2

    assert cleared.returncode == 0
    assert "stopped running injector for lock_wait.long_transaction" in cleared.stdout
    assert injector.wait(timeout=10) == 0  # the injector exited cleanly
    assert seconds_from_clear_to_no_lock_waits <= 15
    assert fetch_value(diag_connection, "SELECT COUNT(*) FROM information_schema.INNODB_TRX "
                                        "WHERE trx_rows_locked >= 50000") == 0
