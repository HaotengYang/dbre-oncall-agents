"""Completion criteria 1, 2 and 8: stop, start and reset.

These tests restart the lab, so they are slow and run only on request
(make test-lifecycle).
"""

import subprocess
import time

import pytest

from tests.lab.conftest import REPOSITORY_ROOT, fetch_value

pytestmark = pytest.mark.lifecycle

STARTUP_LIMIT_SECONDS = 120
MARKER_TABLE = "shop_shadow.lifecycle_marker"


def run_make(target_name):
    started_at = time.monotonic()
    completed = subprocess.run(
        ["make", target_name], cwd=REPOSITORY_ROOT, capture_output=True, text=True
    )
    elapsed_seconds = time.monotonic() - started_at
    assert completed.returncode == 0, completed.stderr[-2000:]
    return elapsed_seconds


def healthy_service_count():
    listing = subprocess.run(
        ["docker", "compose", "ps", "--format", "{{.Service}} {{.Health}}"],
        cwd=REPOSITORY_ROOT, check=True, capture_output=True, text=True,
    ).stdout.split("\n")
    return sum(1 for line in listing if line.endswith(" healthy"))


def test_stop_and_start_keeps_data_and_reset_restores_the_seed(connect_as):
    run_make("up")
    gate_connection = connect_as("shop_gate")
    with gate_connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {MARKER_TABLE}")
        cursor.execute(f"CREATE TABLE {MARKER_TABLE} (id INT PRIMARY KEY)")
    gate_connection.close()

    run_make("down")
    assert healthy_service_count() == 0
    restart_seconds = run_make("up")

    assert restart_seconds < STARTUP_LIMIT_SECONDS
    assert healthy_service_count() == 4
    after_restart = connect_as("shop_gate")
    # "down" then "up" keeps the data volume: the marker is still there.
    assert fetch_value(
        after_restart,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = 'shop_shadow' AND TABLE_NAME = 'lifecycle_marker'",
    ) == 1
    after_restart.close()

    reset_seconds = run_make("reset")

    assert reset_seconds < STARTUP_LIMIT_SECONDS
    assert healthy_service_count() == 4
    after_reset = connect_as("shop_gate")
    # "reset" rebuilds from the seed: the marker is gone, the counts are exact.
    assert fetch_value(
        after_reset,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = 'shop_shadow' AND TABLE_NAME = 'lifecycle_marker'",
    ) == 0
    assert fetch_value(after_reset, "SELECT COUNT(*) FROM shop.orders") == 200_000
    assert fetch_value(after_reset, "SELECT COUNT(*) FROM shop.order_items") == 400_000
