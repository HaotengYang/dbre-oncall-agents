"""Completion criteria 13 and 14: the idle-flood fault really uses up the
server's connections, and stopping it really frees them."""

import time

import pymysql
import pytest

from tests.lab.conftest import fetch_value
from tests.lab.fault_helpers import clear_and_wait_for_baseline, run_injector, start_fault, stop_fault, wait_until

pytestmark = pytest.mark.lab

ALL_SESSIONS = (
    "SELECT COUNT(*) FROM performance_schema.processlist "
    "WHERE USER NOT IN ('event_scheduler', 'system user')"
)
IDLE_APP_SESSIONS = (
    "SELECT COUNT(*) FROM performance_schema.processlist "
    "WHERE USER = 'shop_app' AND COMMAND = 'Sleep'"
)
TOO_MANY_CONNECTIONS = 1040


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def test_idle_flood_exhausts_connections_and_stop_frees_them(connect_as, lab_env):
    # Opened before the fault: afterwards no new connection can be made.
    diag_connection = connect_as("shop_diag")
    max_connections = fetch_value(diag_connection, "SELECT @@global.max_connections")
    sessions_before = fetch_value(diag_connection, ALL_SESSIONS)
    assert sessions_before < 10

    injector = start_fault("connection_exhaustion.idle_flood")
    try:
        sessions_during = fetch_value(diag_connection, ALL_SESSIONS)
        idle_app_sessions_during = fetch_value(diag_connection, IDLE_APP_SESSIONS)
        with pytest.raises(pymysql.err.OperationalError) as refused:
            pymysql.connect(
                host="127.0.0.1", port=int(lab_env.get("MYSQL_HOST_PORT", "13306")),
                user="shop_app", password=lab_env["APP_DB_PASSWORD"], connect_timeout=5,
            )
        # A session that was already connected keeps working.
        assert fetch_value(diag_connection, "SELECT COUNT(*) FROM shop.products") == 1_000

        # Criterion 14: within 10 s of "clear" connections are back under 10.
        # The clock starts before clear is requested.
        clear_requested_at = time.monotonic()
        cleared = run_injector("clear")
        wait_until(lambda: fetch_value(diag_connection, ALL_SESSIONS) < 10,
                   timeout_seconds=10, interval_seconds=0.5)
        seconds_from_clear_to_freed = time.monotonic() - clear_requested_at
    finally:
        if injector.poll() is None:
            stop_fault(injector)

    assert refused.value.args[0] == TOO_MANY_CONNECTIONS
    assert sessions_during >= 0.9 * max_connections
    # The extra sessions are idle application connections, as the fault says.
    assert idle_app_sessions_during >= sessions_during - sessions_before - 2

    assert cleared.returncode == 0
    assert "stopped running injector for connection_exhaustion.idle_flood" in cleared.stdout
    assert injector.wait(timeout=10) == 0
    assert seconds_from_clear_to_freed <= 10
    reconnected = connect_as("shop_app")
    assert fetch_value(reconnected, "SELECT 1") == 1


def test_flood_stays_full_and_only_the_admin_privileged_account_gets_the_reserved_slot(
    observer_connection, lab_env
):
    """Two measured facts the eval cases depend on.

    1. The flood keeps every slot taken for as long as it runs, also while
       other clients come and go.
    2. With connections exhausted, the diagnosis account that holds
       SERVICE_CONNECTION_ADMIN can still take the single connection MySQL
       keeps in reserve on the normal port; the account without it cannot.
    """
    port = int(lab_env.get("MYSQL_HOST_PORT", "13306"))
    max_connections = fetch_value(observer_connection, "SELECT @@global.max_connections")

    def try_normal_port(user_name):
        try:
            return pymysql.connect(host="127.0.0.1", port=port, user=user_name,
                                   password=lab_env["DIAG_DB_PASSWORD"], connect_timeout=5)
        except pymysql.err.OperationalError as refused:
            return refused.args[0]

    injector = start_fault("connection_exhaustion.idle_flood")
    try:
        refusals_without_privilege = []
        for _ in range(20):
            attempt = try_normal_port("shop_diag_noadmin")
            refusals_without_privilege.append(attempt)
            if not isinstance(attempt, int):
                attempt.close()
            time.sleep(1)

        privileged_connection = try_normal_port("shop_diag")
        sessions_with_reserved_slot_taken = fetch_value(observer_connection, ALL_SESSIONS)
        second_privileged_attempt = try_normal_port("shop_diag")
    finally:
        if not isinstance(privileged_connection, int):
            privileged_connection.close()
        stop_fault(injector)

    # Locked out on every one of 20 attempts across 20 s: the flood stays full.
    assert refusals_without_privilege == [TOO_MANY_CONNECTIONS] * 20
    # The privileged account got the reserved connection: one above the limit.
    assert not isinstance(privileged_connection, int)
    assert sessions_with_reserved_slot_taken >= max_connections + 1
    # There is exactly one such connection; a second attempt is refused.
    assert second_privileged_attempt == TOO_MANY_CONNECTIONS
