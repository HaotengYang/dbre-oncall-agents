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


def run_make_expecting_failure(target_name, environment_overrides=None):
    import os

    completed = subprocess.run(
        ["make", target_name],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, **(environment_overrides or {})},
    )
    assert completed.returncode != 0, "make %s unexpectedly succeeded" % target_name
    return completed.stderr


def test_failed_first_start_is_never_reported_healthy(connect_as):
    """A first start that fails part-way leaves an incomplete data volume.
    The next "make up" must fail and say how to recover, not report success."""
    subprocess.run(
        ["docker", "compose", "down", "--volumes"],
        cwd=REPOSITORY_ROOT, check=True, capture_output=True,
    )
    try:
        # "!" is outside the allowed password alphabet, so account creation
        # refuses to run after the schema and seed have already been written.
        run_make_expecting_failure("up", {"APP_DB_PASSWORD": "invalid!password"})

        # Second attempt with the correct .env: the volume now exists, so the
        # image skips initialisation and the accounts are still missing.
        error_output = run_make_expecting_failure("up")

        assert "make reset" in error_output
        assert healthy_service_count() == 0
    finally:
        run_make("reset")

    assert healthy_service_count() == 4
    gate_connection = connect_as("shop_gate")
    assert fetch_value(gate_connection, "SELECT COUNT(*) FROM shop.orders") == 200_000
