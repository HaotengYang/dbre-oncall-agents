"""Wait until the lab is healthy again, instead of sleeping a fixed time.

Uses instantaneous signals only. Latency percentiles are computed over a time
window and stay high until the window has rolled past a fault, long after the
database itself has recovered, so they are not used here.

What the gate is for: "has the lab recovered after a fault was cleared". It is
not proof that no fault is active. A dropped index, or the first seconds of a
gradual connection leak, do not trip any of these signals.
"""

import time
from dataclasses import dataclass
from typing import Optional

MAX_HEALTHY_SESSIONS = 29  # under half of the lab's max_connections of 60
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_STABLE_SECONDS = 5
DEFAULT_POLL_SECONDS = 1


class BaselineNotRestored(Exception):
    """The lab did not become healthy within the time limit."""


@dataclass(frozen=True)
class LabReading:
    """One look at the lab. None means the value could not be obtained."""

    database_reachable: bool
    connected_sessions: Optional[int]
    lock_waits: Optional[int]
    long_statements: Optional[int]
    exporter_up: Optional[bool]
    recent_workload_errors: Optional[float]
    workload_operations_per_second: Optional[float]
    workload_scrape_up: Optional[bool]


def unhealthy_reasons(lab_reading):
    """Returns why the lab is not healthy; an empty list means it is.

    A value that is missing counts as unhealthy. A metric that has disappeared
    is evidence that something is wrong, never that everything is fine.
    """
    if not lab_reading.database_reachable:
        return ["cannot connect to MySQL"]
    reasons = []
    if lab_reading.connected_sessions is None:
        reasons.append("session count unavailable")
    elif lab_reading.connected_sessions > MAX_HEALTHY_SESSIONS:
        reasons.append(f"{lab_reading.connected_sessions} sessions connected")
    if lab_reading.lock_waits is None:
        reasons.append("lock wait count unavailable")
    elif lab_reading.lock_waits > 0:
        reasons.append(f"{lab_reading.lock_waits} lock waits")
    if lab_reading.long_statements is None:
        reasons.append("running statements unavailable")
    elif lab_reading.long_statements > 0:
        reasons.append(f"{lab_reading.long_statements} statement(s) running over 1 s")
    if lab_reading.exporter_up is not True:
        reasons.append("metrics exporter cannot reach MySQL or its metric is missing")
    if lab_reading.recent_workload_errors is None:
        reasons.append("workload error metric is missing")
    elif lab_reading.recent_workload_errors > 0:
        reasons.append(f"workload had {lab_reading.recent_workload_errors:.0f} recent errors")
    # "No errors" proves nothing if the workload is not running at all: its
    # error series would simply be absent. It must be seen doing work. The
    # scrape status reacts within one scrape interval; the throughput rate
    # only once its window has emptied, so both are checked.
    if lab_reading.workload_scrape_up is not True:
        reasons.append("workload metrics cannot be scraped or their status is missing")
    if lab_reading.workload_operations_per_second is None:
        reasons.append("workload throughput metric is missing")
    elif lab_reading.workload_operations_per_second <= 0:
        reasons.append("workload is completing no operations")
    return reasons


def wait_for_healthy_baseline(
    read_state,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    stable_seconds=DEFAULT_STABLE_SECONDS,
    poll_seconds=DEFAULT_POLL_SECONDS,
    now=time.monotonic,
    sleep=time.sleep,
):
    """Polls read_state() until the lab has been healthy for stable_seconds.

    Returns the seconds waited. Raises BaselineNotRestored at the timeout,
    naming the last reasons. A read that raises counts as unhealthy.
    """
    started_at = now()
    healthy_since = None
    last_reasons = ["no reading taken yet"]
    while True:
        try:
            last_reasons = unhealthy_reasons(read_state())
        except Exception as read_error:
            last_reasons = [f"could not read lab state: {read_error}"]

        current_time = now()
        if last_reasons:
            healthy_since = None
        else:
            if healthy_since is None:
                healthy_since = current_time
            if current_time - healthy_since >= stable_seconds:
                return current_time - started_at

        if current_time - started_at >= timeout_seconds:
            raise BaselineNotRestored(
                f"lab not healthy after {timeout_seconds} s: " + "; ".join(last_reasons)
            )
        sleep(poll_seconds)
