"""Completion criteria 1 to 7 of the fault injector, for every registered fault.

New faults are picked up automatically from the registry.
"""

import json
import signal

import pytest

from fault_injector.registry import ROOT_CAUSE_LABELS, list_faults
from lab_health.lab_state import wait_for_lab_baseline
from tests.lab.conftest import fetch_value
from tests.lab.fault_helpers import (
    clear_and_wait_for_baseline,
    read_injection_events,
    STATE_DIRECTORY,
    lab_looks_healthy,
    row_counts,
    run_injector,
    schema_snapshot,
    start_fault,
    stop_fault,
    wait_until,
)

pytestmark = pytest.mark.lab

ALL_FAULT_IDS = [fault.fault_id for fault in list_faults()]
RECOVERY_LIMIT_SECONDS = 60


read_events = read_injection_events


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


@pytest.mark.parametrize("fault_id", ALL_FAULT_IDS)
def test_inject_then_stop_leaves_schema_and_data_unchanged(observer_connection, fault_id):
    diag_connection = observer_connection
    schema_before = schema_snapshot(diag_connection)
    counts_before = row_counts(diag_connection)

    injector = start_fault(fault_id)
    exit_code = stop_fault(injector)

    assert exit_code == 0
    assert schema_snapshot(diag_connection) == schema_before
    assert row_counts(diag_connection) == counts_before
    wait_for_lab_baseline(stable_seconds=3, timeout_seconds=RECOVERY_LIMIT_SECONDS)


@pytest.mark.parametrize("fault_id", ALL_FAULT_IDS)
def test_killed_injector_is_cleaned_up_by_clear(observer_connection, fault_id):
    diag_connection = observer_connection
    schema_before = schema_snapshot(diag_connection)
    events_before = len(read_events())

    injector = start_fault(fault_id)
    injector.send_signal(signal.SIGKILL)  # no chance to clean up
    injector.wait(timeout=10)

    refused = run_injector("inject", fault_id, "--duration", "1")
    assert refused.returncode == 2
    assert "clear" in refused.stderr

    cleared = run_injector("clear")

    assert cleared.returncode == 0
    assert schema_snapshot(diag_connection) == schema_before
    wait_for_lab_baseline(stable_seconds=3, timeout_seconds=RECOVERY_LIMIT_SECONDS)
    new_events = read_events()[events_before:]
    assert [event["event"] for event in new_events] == ["started", "ended"]
    assert new_events[1]["ended_by"] == "clear"
    assert new_events[0]["injection_id"] == new_events[1]["injection_id"]


@pytest.mark.parametrize("fault_id", ALL_FAULT_IDS)
def test_second_inject_is_refused_while_a_fault_is_active(fault_id):
    injector = start_fault(fault_id)
    try:
        events_during = len(read_events())

        second = run_injector("inject", ALL_FAULT_IDS[0], "--duration", "1")

        assert second.returncode == 2
        assert "already active" in second.stderr
        assert len(read_events()) == events_during  # the refusal recorded nothing
        assert injector.poll() is None  # the first fault is still running
    finally:
        stop_fault(injector)


@pytest.mark.parametrize("fault_id", ALL_FAULT_IDS)
def test_injection_is_recorded_with_label_and_times(fault_id):
    events_before = len(read_events())

    injector = start_fault(fault_id)
    stop_fault(injector)

    started, ended = read_events()[events_before:]
    assert started["event"] == "started" and ended["event"] == "ended"
    assert started["fault_id"] == fault_id
    assert started["root_cause"] in ROOT_CAUSE_LABELS
    assert started["changes"]
    assert started["started_at"] < ended["ended_at"]  # ISO timestamps sort as text
    assert ended["ended_by"] == "injector"


def test_clear_on_a_healthy_lab_changes_nothing(connect_as):
    diag_connection = connect_as("shop_diag")
    schema_before = schema_snapshot(diag_connection)
    events_before = read_events()

    first = run_injector("clear")
    second = run_injector("clear")

    assert first.returncode == 0 and second.returncode == 0
    assert "already at baseline" in second.stdout
    assert schema_snapshot(diag_connection) == schema_before
    assert read_events() == events_before


@pytest.mark.parametrize("fault_id", ALL_FAULT_IDS)
def test_injector_refuses_a_server_without_the_lab_marker(connect_as, fault_id):
    root_connection = connect_as("root")
    diag_connection = connect_as("shop_diag")
    schema_before = schema_snapshot(diag_connection)
    sessions_before = fetch_value(
        diag_connection, "SELECT COUNT(*) FROM performance_schema.processlist"
    )
    events_before = read_events()

    with root_connection.cursor() as cursor:
        cursor.execute("UPDATE lab_meta.environment SET name = 'not-the-lab'")
    try:
        refused = run_injector("inject", fault_id, "--duration", "5")
        clear_refused = run_injector("clear")
    finally:
        with root_connection.cursor() as cursor:
            cursor.execute("UPDATE lab_meta.environment SET name = 'dbre-lab'")

    assert refused.returncode == 2
    assert "lab marker" in refused.stderr
    assert clear_refused.returncode == 2
    assert schema_snapshot(diag_connection) == schema_before
    assert read_events() == events_before
    assert fetch_value(
        diag_connection, "SELECT COUNT(*) FROM performance_schema.processlist"
    ) <= sessions_before + 1
