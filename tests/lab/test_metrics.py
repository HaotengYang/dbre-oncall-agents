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
    slowest_p99_seconds = single_value(
        prometheus_query(
            "max(histogram_quantile(0.99, "
            "sum by (le, operation) (rate(workload_operation_duration_seconds_bucket[1m]))))"
        )
    )

    assert slowest_p99_seconds < 0.1
