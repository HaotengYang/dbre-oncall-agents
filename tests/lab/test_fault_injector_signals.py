"""The injector must stop promptly on Ctrl-C however the signal lands.

An earlier version set a threading.Event from the signal handler while the
main thread polled Event.wait(); when the signal arrived at the wrong moment
the process deadlocked and held the injector lock forever.
"""

import signal
import time

import pytest

from tests.lab.fault_helpers import run_injector, start_fault

pytestmark = pytest.mark.lab

ATTEMPTS = 12
STOP_LIMIT_SECONDS = 20


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    assert run_injector("clear").returncode == 0
    yield
    assert run_injector("clear").returncode == 0


def test_injector_stops_on_repeated_signals_at_varied_moments():
    for attempt in range(ATTEMPTS):
        injector = start_fault("lock_wait.long_transaction")
        # Vary the offset so signals land at different points of the wait loop,
        # and send several in a row, as an impatient Ctrl-C does.
        time.sleep(0.013 * attempt)
        stop_requested_at = time.monotonic()
        for _ in range(5):
            injector.send_signal(signal.SIGINT)
            time.sleep(0.01)
        try:
            exit_code = injector.wait(timeout=STOP_LIMIT_SECONDS)
        finally:
            if injector.poll() is None:
                injector.kill()

        assert exit_code == 0, f"attempt {attempt}"
        assert time.monotonic() - stop_requested_at < STOP_LIMIT_SECONDS
