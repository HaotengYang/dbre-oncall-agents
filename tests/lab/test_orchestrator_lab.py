"""The orchestrator against the real lab, end to end."""

import json
import subprocess
import sys

import pytest

from diagnosis.access import load_diagnosis_access
from diagnosis.orchestrator import orchestrate
from tests.lab.conftest import REPOSITORY_ROOT
from tests.lab.fault_helpers import clear_and_wait_for_baseline, start_fault, stop_fault

pytestmark = pytest.mark.lab


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def test_lock_wait_is_diagnosed_in_one_round_with_both_agents_reporting():
    injector = start_fault("lock_wait.long_transaction")
    try:
        report = orchestrate("Order updates are timing out.", load_diagnosis_access())
    finally:
        stop_fault(injector)

    assert report.diagnosis.root_cause == "lock_wait"
    assert report.diagnosis.rounds_used == 1
    sql_finding = next(f for f in report.findings if f.agent == "sql")
    assert sql_finding.candidate == "lock_wait"
    assert any(b["rows_locked"] >= 50_000 and b["holder_is_idle"]
               for b in sql_finding.details["blocking_transactions"])


def test_locked_out_without_admin_port_escalates_after_three_rounds():
    injector = start_fault("connection_exhaustion.idle_flood")
    try:
        report = orchestrate("The app cannot connect.",
                             load_diagnosis_access(admin_port_available=False),
                             seconds_between_rounds=2)
    finally:
        stop_fault(injector)

    assert report.diagnosis.root_cause == "undetermined"
    assert report.diagnosis.rounds_used == 3
    assert report.diagnosis.symptoms == ("connection_exhaustion",)
    assert "escalate to a human" in report.diagnosis.rationale.lower()


def test_command_line_prints_a_json_report_for_a_healthy_lab():
    completed = subprocess.run(
        [sys.executable, "-m", "diagnosis", "Is the database fine?", "--json"],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["root_cause"] == "healthy" and report["rounds_used"] == 1
    assert {finding["agent"] for finding in report["findings"]} == {"sql", "metrics"}
