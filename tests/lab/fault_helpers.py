"""Helpers for lab tests that run the fault injector as a real process."""

import pathlib
import signal
import subprocess
import sys
import time

from tests.lab.conftest import REPOSITORY_ROOT, fetch_all, fetch_value

STATE_DIRECTORY = REPOSITORY_ROOT / "var"
SHOP_TABLES = ("customers", "products", "orders", "order_items")
INJECTOR_COMMAND = [sys.executable, "-m", "fault_injector"]


def run_injector(*arguments):
    """Runs an injector command to completion and returns the finished process."""
    return subprocess.run(
        [*INJECTOR_COMMAND, *arguments], cwd=REPOSITORY_ROOT,
        capture_output=True, text=True, timeout=120,
    )


def start_fault(fault_id, startup_timeout_seconds=60):
    """Starts "inject" in the background and returns once the fault is active."""
    process = subprocess.Popen(
        [*INJECTOR_COMMAND, "inject", fault_id], cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + startup_timeout_seconds
    while time.monotonic() < deadline:
        output_line = process.stdout.readline()
        if "fault is active" in output_line:
            return process
        if output_line == "" and process.poll() is not None:
            raise AssertionError(f"injector exited early: {process.stderr.read()}")
    process.kill()
    raise AssertionError("injector did not report an active fault in time")


def stop_fault(process):
    """Stops a running injector the way Ctrl-C would and waits for cleanup."""
    process.send_signal(signal.SIGINT)
    process.wait(timeout=90)
    return process.returncode


def schema_snapshot(connection):
    """The exact CREATE TABLE text of every shop table."""
    return {
        table_name: fetch_all(connection, f"SHOW CREATE TABLE shop.{table_name}")[0][1]
        for table_name in SHOP_TABLES
    }


def row_counts(connection):
    return {
        table_name: fetch_value(connection, f"SELECT COUNT(*) FROM shop.{table_name}")
        for table_name in SHOP_TABLES
    }


def wait_until(condition, timeout_seconds, interval_seconds=1.0):
    """Polls until condition() is true. Returns the seconds it took, or fails."""
    started_at = time.monotonic()
    while time.monotonic() - started_at < timeout_seconds:
        if condition():
            return time.monotonic() - started_at
        time.sleep(interval_seconds)
    raise AssertionError(f"condition not met within {timeout_seconds} s")


def lab_looks_healthy(connection):
    """The same signals as the lab's healthy-baseline test."""
    connected_sessions = fetch_value(
        connection, "SELECT COUNT(*) FROM performance_schema.processlist"
    )
    lock_waits = fetch_value(connection, "SELECT COUNT(*) FROM performance_schema.data_lock_waits")
    long_statements = fetch_value(
        connection,
        "SELECT COUNT(*) FROM performance_schema.processlist "
        "WHERE COMMAND NOT IN ('Sleep', 'Daemon', 'Binlog Dump') "
        "AND USER NOT IN ('event_scheduler', 'system user') AND TIME > 1",
    )
    return connected_sessions < 30 and lock_waits == 0 and long_statements == 0


def digest_totals(connection, digest_text):
    """Executions and rows examined so far for one normalized statement."""
    rows = fetch_all(
        connection,
        "SELECT COUNT_STAR, SUM_ROWS_EXAMINED "
        "FROM performance_schema.events_statements_summary_by_digest "
        "WHERE SCHEMA_NAME = 'shop' AND DIGEST_TEXT = %s AND COUNT_STAR > 0",
        (digest_text,),
    )
    assert len(rows) == 1, rows
    return int(rows[0][0]), int(rows[0][1])


def rows_examined_per_execution(connection, digest_text, window_seconds):
    executions_before, examined_before = digest_totals(connection, digest_text)
    time.sleep(window_seconds)
    executions_after, examined_after = digest_totals(connection, digest_text)
    executions = executions_after - executions_before
    assert executions > 0, "the statement did not run during the window"
    return (examined_after - examined_before) / executions
