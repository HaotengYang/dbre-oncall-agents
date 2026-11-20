"""Fixed-rate pacing for workload workers."""

import time


class RatePacer:
    """Releases one operation per interval, on a fixed schedule.

    Slots are scheduled from the previous slot, not from when the previous
    operation finished, so slow operations do not lower the rate. If the caller
    falls behind by more than one interval (for example the database was
    unreachable), the schedule restarts from the current time instead of
    firing a burst to catch up. A burst after an outage would itself look like
    a fault to the diagnosis system.
    """

    def __init__(self, operations_per_second, now=time.monotonic, sleep=time.sleep):
        if operations_per_second <= 0:
            raise ValueError(
                f"operations_per_second must be positive, got {operations_per_second}"
            )
        self._interval_seconds = 1.0 / operations_per_second
        self._now = now
        self._sleep = sleep
        self._next_slot_at = None

    def wait_for_next_slot(self):
        current_time = self._now()
        if self._next_slot_at is None:
            self._next_slot_at = current_time
        if current_time - self._next_slot_at > self._interval_seconds:
            self._next_slot_at = current_time

        delay_seconds = self._next_slot_at - current_time
        if delay_seconds > 0:
            self._sleep(delay_seconds)
        self._next_slot_at += self._interval_seconds
