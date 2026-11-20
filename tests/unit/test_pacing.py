"""The pacer must hold a steady rate and must not burst after a stall."""

import pytest

from shop_workload.pacing import RatePacer


class FakeClock:
    """A clock that only moves when the pacer sleeps or the test advances it."""

    def __init__(self):
        self.now_seconds = 100.0
        self.sleep_calls = []

    def now(self):
        return self.now_seconds

    def sleep(self, duration_seconds):
        self.sleep_calls.append(duration_seconds)
        self.now_seconds += duration_seconds


def test_first_slot_is_immediate():
    clock = FakeClock()
    pacer = RatePacer(operations_per_second=10, now=clock.now, sleep=clock.sleep)

    pacer.wait_for_next_slot()

    assert clock.now_seconds == 100.0


def test_slots_are_spaced_by_the_interval():
    clock = FakeClock()
    pacer = RatePacer(operations_per_second=10, now=clock.now, sleep=clock.sleep)

    for _ in range(51):
        pacer.wait_for_next_slot()

    # 51 slots at 10 per second span 50 intervals of 0.1 s.
    assert clock.now_seconds == pytest.approx(105.0)


def test_slow_operations_do_not_add_to_the_interval():
    clock = FakeClock()
    pacer = RatePacer(operations_per_second=10, now=clock.now, sleep=clock.sleep)

    for _ in range(11):
        pacer.wait_for_next_slot()
        clock.now_seconds += 0.04  # the operation itself takes 40 ms

    # Slot 11 starts at 100 + 10 * 0.1 = 101.0, then its operation adds 0.04.
    assert clock.now_seconds == pytest.approx(101.04)


def test_no_burst_after_a_long_stall():
    clock = FakeClock()
    pacer = RatePacer(operations_per_second=10, now=clock.now, sleep=clock.sleep)
    pacer.wait_for_next_slot()

    clock.now_seconds += 30.0  # the database was unreachable for 30 s
    stall_ended_at = clock.now_seconds
    for _ in range(11):
        pacer.wait_for_next_slot()

    # Without protection the pacer would fire ~300 operations with no sleep to
    # "catch up". With it, the 11 slots after the stall still span one second.
    assert clock.now_seconds - stall_ended_at == pytest.approx(1.0)


@pytest.mark.parametrize("invalid_rate", [0, -1, -0.5])
def test_rate_must_be_positive(invalid_rate):
    with pytest.raises(ValueError, match="operations_per_second"):
        RatePacer(operations_per_second=invalid_rate)
