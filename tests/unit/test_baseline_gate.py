"""The gate decides when the lab is healthy again after a fault. It must wait
for a stable healthy state, give up after a limit, and say why."""

import pytest

from lab_health.baseline_gate import (
    BaselineNotRestored,
    LabReading,
    unhealthy_reasons,
    wait_for_healthy_baseline,
)

HEALTHY = LabReading(
    database_reachable=True, connected_sessions=5, lock_waits=0, long_statements=0,
    exporter_up=True, recent_workload_errors=0.0,
)


def reading(**changes):
    return LabReading(**{**HEALTHY.__dict__, **changes})


class FakeClock:
    def __init__(self):
        self.now_seconds = 0.0

    def now(self):
        return self.now_seconds

    def sleep(self, duration_seconds):
        self.now_seconds += duration_seconds


def readings_in_order(*readings):
    remaining = list(readings)

    def read_state():
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return read_state


def test_healthy_reading_has_no_reasons():
    assert unhealthy_reasons(HEALTHY) == []


@pytest.mark.parametrize(
    "changes, expected_fragment",
    [
        ({"database_reachable": False}, "cannot connect"),
        ({"connected_sessions": 30}, "30 sessions"),
        ({"lock_waits": 2}, "2 lock waits"),
        ({"long_statements": 1}, "1 statement"),
        ({"exporter_up": False}, "exporter"),
        ({"recent_workload_errors": 3.0}, "workload"),
    ],
)
def test_each_unhealthy_signal_is_named(changes, expected_fragment):
    reasons = unhealthy_reasons(reading(**changes))

    assert len(reasons) == 1
    assert expected_fragment in reasons[0]


def test_missing_exporter_metric_counts_as_unhealthy_not_as_fine():
    # A metric that disappeared is evidence of trouble, never of health.
    assert unhealthy_reasons(reading(exporter_up=None)) != []
    assert unhealthy_reasons(reading(recent_workload_errors=None)) != []


def test_returns_once_healthy_for_the_whole_stable_period():
    clock = FakeClock()

    waited = wait_for_healthy_baseline(
        readings_in_order(HEALTHY), timeout_seconds=60, stable_seconds=5, poll_seconds=1,
        now=clock.now, sleep=clock.sleep,
    )

    assert waited == pytest.approx(5)


def test_a_relapse_restarts_the_stable_period():
    clock = FakeClock()
    sequence = readings_in_order(
        reading(lock_waits=1),  # t=0 unhealthy
        HEALTHY, HEALTHY, HEALTHY,  # t=1..3 healthy
        reading(lock_waits=1),  # t=4 relapse
        HEALTHY,  # t=5 onwards healthy
    )

    waited = wait_for_healthy_baseline(
        sequence, timeout_seconds=60, stable_seconds=5, poll_seconds=1,
        now=clock.now, sleep=clock.sleep,
    )

    # Healthy again from t=5, stable for 5 s, so it returns at t=10, not t=6.
    assert waited == pytest.approx(10)


def test_gives_up_at_the_timeout_and_reports_the_last_reasons():
    clock = FakeClock()

    with pytest.raises(BaselineNotRestored) as raised:
        wait_for_healthy_baseline(
            readings_in_order(reading(lock_waits=3, connected_sessions=58)),
            timeout_seconds=20, stable_seconds=5, poll_seconds=1,
            now=clock.now, sleep=clock.sleep,
        )

    assert clock.now_seconds == pytest.approx(20, abs=1)
    assert "3 lock waits" in str(raised.value)
    assert "58 sessions" in str(raised.value)


def test_a_reading_that_raises_counts_as_unhealthy_and_is_retried():
    clock = FakeClock()
    calls = {"count": 0}

    def flaky_read_state():
        calls["count"] += 1
        if calls["count"] <= 2:
            raise ConnectionError("prometheus not answering")
        return HEALTHY

    waited = wait_for_healthy_baseline(
        flaky_read_state, timeout_seconds=60, stable_seconds=3, poll_seconds=1,
        now=clock.now, sleep=clock.sleep,
    )

    assert waited == pytest.approx(5)  # two failed reads, then 3 s stable
