"""Completion criteria 4, 5 and 6: metrics flow, the workload holds its rate,
and the idle baseline is healthy."""

import time

import pytest

from tests.lab.conftest import fetch_all, fetch_value

pytestmark = pytest.mark.lab

OBSERVATION_WINDOW_SECONDS = 60
RATE_TOLERANCE = 0.20
OK_OPERATIONS = 'sum(workload_operations_total{outcome="ok"})'
FAILED_OPERATIONS = 'sum(workload_operations_total{outcome="error"}) or vector(0)'


def single_value(samples):
    assert len(samples) == 1, samples
    return samples[0][1]


@pytest.mark.parametrize("job_name", ["mysql", "workload"])
def test_scrape_target_is_up(prometheus_query, job_name):
    assert single_value(prometheus_query(f'up{{job="{job_name}"}}')) == 1


def test_exporter_can_reach_mysql(prometheus_query):
    # "up" only says the exporter answered. mysql_up says it reached the server.
    assert single_value(prometheus_query("mysql_up")) == 1


def test_connection_count_metric_has_a_plausible_value(prometheus_query, connect_as):
    connected_threads = single_value(prometheus_query("mysql_global_status_threads_connected"))
    max_connections = single_value(prometheus_query("mysql_global_variables_max_connections"))

    assert max_connections == 60
    assert 1 <= connected_threads <= max_connections


def test_workload_holds_its_configured_rate(prometheus_query, lab_env):
    configured_rate = float(lab_env.get("WORKLOAD_OPERATIONS_PER_SECOND", "20"))

    operations_before = single_value(prometheus_query(OK_OPERATIONS))
    failures_before = single_value(prometheus_query(FAILED_OPERATIONS))
    window_started_at = time.monotonic()
    time.sleep(OBSERVATION_WINDOW_SECONDS)
    operations_after = single_value(prometheus_query(OK_OPERATIONS))
    failures_after = single_value(prometheus_query(FAILED_OPERATIONS))
    window_seconds = time.monotonic() - window_started_at

    observed_rate = (operations_after - operations_before) / window_seconds
    assert observed_rate == pytest.approx(configured_rate, rel=RATE_TOLERANCE)
    assert failures_after - failures_before == 0


def test_idle_baseline_is_healthy(prometheus_query, connect_as):
    connection = connect_as("shop_diag")

    connected_threads = single_value(prometheus_query("mysql_global_status_threads_connected"))
    assert connected_threads < 30  # under half of max_connections

    assert fetch_value(connection, "SELECT COUNT(*) FROM performance_schema.data_lock_waits") == 0

    long_running_statements = fetch_all(
        connection,
        "SELECT ID, USER, TIME, INFO FROM performance_schema.processlist "
        "WHERE COMMAND NOT IN ('Sleep', 'Daemon', 'Binlog Dump') "
        "AND USER NOT IN ('event_scheduler', 'system user') AND TIME > 1",
    )
    assert long_running_statements == ()


def test_baseline_workload_latency_is_low(prometheus_query):
    # Measure a window that starts inside this test. Fault tests that ran just
    # before would otherwise still be inside the rate window and make a
    # healthy lab look slow (seen: 0.167 s right after the injector tests).
    quiet_window_seconds = 30
    time.sleep(quiet_window_seconds + 10)
    slowest_p99_seconds = single_value(
        prometheus_query(
            "max(histogram_quantile(0.99, sum by (le, operation) "
            f"(rate(workload_operation_duration_seconds_bucket[{quiet_window_seconds}s]))))"
        )
    )

    assert slowest_p99_seconds < 0.1


def test_table_lock_does_not_pile_up_workload_sessions(prometheus_query, connect_as):
    """A lock on a busy table must not turn into connection growth by itself.

    Before the workload set server-side lock timeouts, each worker timed out
    client-side, reconnected, and left its old session blocked on the server:
    sessions grew by four every 30 seconds for as long as the lock was held.
    """
    root_connection = connect_as("root")
    observer_connection = connect_as("shop_diag")
    count_sessions = "SELECT COUNT(*) FROM performance_schema.processlist WHERE USER = 'shop_app'"
    sessions_before = fetch_value(observer_connection, count_sessions)
    failures_before = single_value(prometheus_query(FAILED_OPERATIONS))
    lock_held_seconds = 45

    peak_sessions = sessions_before
    with root_connection.cursor() as cursor:
        cursor.execute("LOCK TABLES shop.orders WRITE")
    try:
        lock_released_at = time.monotonic() + lock_held_seconds
        while time.monotonic() < lock_released_at:
            time.sleep(3)
            peak_sessions = max(peak_sessions, fetch_value(observer_connection, count_sessions))
    finally:
        with root_connection.cursor() as cursor:
            cursor.execute("UNLOCK TABLES")

    # One extra is tolerated: a worker can be mid-reconnect when sampled.
    assert peak_sessions <= sessions_before + 1
    # The lock was real and the workload felt it: blocked operations failed
    # and were counted, rather than hanging unseen.
    time.sleep(8)
    assert single_value(prometheus_query(FAILED_OPERATIONS)) - failures_before >= 4
    assert fetch_value(observer_connection, count_sessions) == sessions_before
