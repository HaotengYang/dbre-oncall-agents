"""Completion criteria 13 and 14: the idle-flood fault really uses up the
server's connections, and stopping it really frees them."""

import pymysql
import pytest

from tests.lab.conftest import fetch_value
from tests.lab.fault_helpers import run_injector, start_fault, stop_fault, wait_until

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
    assert run_injector("clear").returncode == 0
    yield
    assert run_injector("clear").returncode == 0


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
    finally:
        stop_fault(injector)

    assert refused.value.args[0] == TOO_MANY_CONNECTIONS
    assert sessions_during >= 0.9 * max_connections
    # The extra sessions are idle application connections, as the fault says.
    assert idle_app_sessions_during >= sessions_during - sessions_before - 2

    seconds_until_freed = wait_until(
        lambda: fetch_value(diag_connection, ALL_SESSIONS) < 10, timeout_seconds=10
    )
    assert seconds_until_freed <= 10
    reconnected = connect_as("shop_app")
    assert fetch_value(reconnected, "SELECT 1") == 1
