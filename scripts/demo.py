"""A live demo in one command (`make demo`), under five minutes.

1. Check the lab is at its healthy baseline.
2. Inject a cascading fault: an unindexed report query piles up until the
   server runs out of connections.
3. Diagnose it from an alert, with no knowledge of what was injected.
4. Stop the fault and wait for the baseline.
5. Rehearse the index the diagnosis suggested in the change safety gate
   (`plan` only: nothing is changed on the real table).
6. Clean up, whatever happened.

The demo is the one place that drives both sides, like the eval runner. The
diagnosis itself still sees only the alert and the live server.
"""

import pathlib
import sys
import time

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from change_gate.allowlist import IndexRequest, RejectedChange  # noqa: E402
from change_gate.gate import GateError, format_plan, load_gate_access, make_plan  # noqa: E402
from diagnosis.access import load_diagnosis_access  # noqa: E402
from diagnosis.orchestrator import orchestrate, render_report_text  # noqa: E402
from fault_injector.engine import clear_lab, injected_fault  # noqa: E402
from lab_health.lab_state import wait_for_lab_baseline  # noqa: E402

DEMO_FAULT_ID = "cascade.slow_query_exhausts_connections"
DEMO_ALERT = "The app cannot get database connections and database CPU is pinned."
OBSERVE_SECONDS = 45
# The report query the faulty application runs, with literal values, so the
# gate can check on its sample copy that the new index would be used.
TARGET_QUERY = (
    "SELECT order_id, quantity, unit_price_cents FROM order_items "
    "WHERE product_id = 7 AND quantity >= 2 ORDER BY unit_price_cents DESC LIMIT 50"
)


def banner(step_number, title):
    print(f"\n{'=' * 72}\n[{step_number}/6] {title}\n{'=' * 72}", flush=True)


def countdown(seconds, sleep=time.sleep):
    for remaining in range(seconds, 0, -5):
        print(f"  letting the fault develop... {remaining:>2} s left", flush=True)
        sleep(min(5, remaining))


def rehearse_suggestion(suggestion):
    if suggestion is None:
        print("The diagnosis suggested no index, so there is nothing to rehearse.")
        return
    request = IndexRequest(suggestion.table, tuple(suggestion.columns), None)
    try:
        plan = make_plan(load_gate_access(), request, TARGET_QUERY)
    except (RejectedChange, GateError) as refused:
        print(f"The gate refused the suggestion: {refused}")
        return
    print(format_plan(plan))
    print("\n(plan only: the real table was not changed. "
          "`python -m change_gate apply ...` would ask for confirmation.)")


def run_demo(sleep=time.sleep):
    started = time.monotonic()
    report = None
    try:
        banner(1, "Startup check: is the lab at its healthy baseline?")
        # A claim left by a killed earlier run would refuse the injection.
        for action in clear_lab():
            print(f"  {action}")
        wait_for_lab_baseline()
        print("Lab is healthy: MySQL, Prometheus, exporter and workload all at baseline.")

        banner(2, f"Inject a cascading fault: {DEMO_FAULT_ID}")
        with injected_fault(DEMO_FAULT_ID):
            print("An unindexed report query now arrives on new connections faster "
                  "than it finishes.")
            countdown(OBSERVE_SECONDS, sleep=sleep)

            banner(3, f'Diagnose from the alert: "{DEMO_ALERT}"')
            report = orchestrate(DEMO_ALERT, load_diagnosis_access(admin_port_available=True))
            print(render_report_text(report))

            banner(4, "Stop the fault and wait for the healthy baseline")
        wait_for_lab_baseline()
        print("Fault stopped; lab back at baseline.")

        banner(5, "Rehearse the suggested index in the change safety gate")
        rehearse_suggestion(report.diagnosis.index_suggestion)
    finally:
        banner(6, "Clean up")
        for action in clear_lab():
            print(f"  {action}")
        print(f"\nDemo finished in {time.monotonic() - started:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(run_demo())
