"""The shared gate against the real lab: passes when healthy, refuses during
each kind of fault, and passes again after the fault is cleared."""

import pytest

from lab_health.baseline_gate import BaselineNotRestored
from lab_health.lab_state import read_lab_state, wait_for_lab_baseline
from tests.lab.fault_helpers import clear_and_wait_for_baseline, start_fault, stop_fault, wait_until

pytestmark = pytest.mark.lab


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def test_healthy_lab_passes_quickly():
    waited_seconds = wait_for_lab_baseline(stable_seconds=3, timeout_seconds=30)

    assert waited_seconds < 10
    lab_reading = read_lab_state()
    assert lab_reading.database_reachable and lab_reading.exporter_up is True
    assert lab_reading.connected_sessions < 10 and lab_reading.lock_waits == 0


@pytest.mark.parametrize(
    "fault_id, expected_reason",
    [
        ("lock_wait.long_transaction", "lock waits"),
        # Either the probe is refused, or it slips into a slot the exporter
        # just gave up and sees every slot taken. Both are "not healthy".
        ("connection_exhaustion.idle_flood", ("cannot connect", "sessions connected")),
    ],
)
def test_gate_refuses_during_a_fault_and_passes_after_it(fault_id, expected_reason):
    acceptable_reasons = (expected_reason,) if isinstance(expected_reason, str) else expected_reason
    injector = start_fault(fault_id)
    try:
        with pytest.raises(BaselineNotRestored) as raised:
            wait_for_lab_baseline(stable_seconds=3, timeout_seconds=25)
        assert any(reason in str(raised.value) for reason in acceptable_reasons), raised.value
    finally:
        stop_fault(injector)

    assert wait_for_lab_baseline(stable_seconds=3, timeout_seconds=60) < 60
