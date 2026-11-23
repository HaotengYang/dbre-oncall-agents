"""The inspection agent against the real lab."""

import time

import pytest

from diagnosis.access import load_diagnosis_access
from diagnosis.inspection import LONG_TRANSACTION_SECONDS, assess_risks, collect_readings
from tests.lab.fault_helpers import clear_and_wait_for_baseline, start_fault, stop_fault

pytestmark = pytest.mark.lab


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def test_the_baseline_shows_no_transaction_or_statement_risk():
    risks = assess_risks(collect_readings(load_diagnosis_access()))
    assert [risk for risk in risks if risk.check in ("long_transaction", "unindexed_statement")] == []
    assert not any("missing" in risk.message for risk in risks)


def test_an_idle_transaction_holding_locks_is_listed_once_it_is_old_enough():
    injector = start_fault("lock_wait.long_transaction")
    try:
        # The transaction has to age past the threshold; this waits for the
        # fault to develop, not for a recovery.
        time.sleep(LONG_TRANSACTION_SECONDS + 5)
        risks = assess_risks(collect_readings(load_diagnosis_access()))
    finally:
        stop_fault(injector)
    long_transactions = [risk for risk in risks if risk.check == "long_transaction"]
    assert long_transactions, risks
    assert long_transactions[0].severity == "high"  # it locks about 50,000 rows
    assert "session is idle" in long_transactions[0].message


def test_a_frequent_unindexed_query_is_listed():
    injector = start_fault("missing_index.unindexed_query")
    try:
        risks = assess_risks(collect_readings(load_diagnosis_access()))
    finally:
        stop_fault(injector)
    unindexed = [risk for risk in risks if risk.check == "unindexed_statement"]
    assert unindexed, risks
    assert "order_items" in unindexed[0].message
