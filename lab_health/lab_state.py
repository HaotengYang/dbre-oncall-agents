"""Reads the lab's current state for the baseline gate."""

import os
import pathlib

import pymysql
import requests

from lab_health.baseline_gate import LabReading, wait_for_healthy_baseline

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKLOAD_ERROR_WINDOW = "15s"


def read_env_file(env_path):
    values = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped_line = line.strip()
            if stripped_line and not stripped_line.startswith("#") and "=" in stripped_line:
                name, _, value = stripped_line.partition("=")
                values[name.strip()] = value.strip()
    return values


def lab_environment():
    merged = read_env_file(REPOSITORY_ROOT / ".env")
    merged.update(os.environ)
    return merged


def _prometheus_value(base_url, promql):
    response = requests.get(f"{base_url}/api/v1/query", params={"query": promql}, timeout=5)
    response.raise_for_status()
    samples = response.json()["data"]["result"]
    return float(samples[0]["value"][1]) if samples else None


def read_lab_state(environment=None):
    """Takes one LabReading using the read-only diagnosis account and Prometheus."""
    environment = environment or lab_environment()
    prometheus_url = f"http://127.0.0.1:{environment.get('PROMETHEUS_HOST_PORT', '19090')}"
    exporter_up_value = _prometheus_value(prometheus_url, "mysql_up")
    recent_workload_errors = _prometheus_value(
        prometheus_url,
        f'sum(increase(workload_operations_total{{outcome="error"}}[{WORKLOAD_ERROR_WINDOW}])) '
        "or vector(0)",
    )
    exporter_up = None if exporter_up_value is None else exporter_up_value == 1

    try:
        connection = pymysql.connect(
            host="127.0.0.1", port=int(environment.get("MYSQL_HOST_PORT", "13306")),
            user="shop_diag", password=environment["DIAG_DB_PASSWORD"],
            connect_timeout=5, read_timeout=10, autocommit=True,
        )
    except pymysql.MySQLError:
        return LabReading(False, None, None, None, exporter_up, recent_workload_errors)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM performance_schema.processlist "
                "WHERE USER NOT IN ('event_scheduler', 'system user')"
            )
            connected_sessions = cursor.fetchone()[0] - 1  # not counting this probe
            cursor.execute("SELECT COUNT(*) FROM performance_schema.data_lock_waits")
            lock_waits = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM performance_schema.processlist "
                "WHERE COMMAND NOT IN ('Sleep', 'Daemon', 'Binlog Dump') "
                "AND USER NOT IN ('event_scheduler', 'system user') AND TIME > 1"
            )
            long_statements = cursor.fetchone()[0]
    finally:
        connection.close()
    return LabReading(True, connected_sessions, lock_waits, long_statements,
                      exporter_up, recent_workload_errors)


def wait_for_lab_baseline(**gate_options):
    """Waits until the real lab is healthy. Returns the seconds waited."""
    return wait_for_healthy_baseline(read_lab_state, **gate_options)
