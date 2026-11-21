"""The eval runner against the real lab: it scores real runs correctly, leaves
the lab at baseline whatever the backend does, and refuses to score a case
that contradicts the injector's own record."""

import dataclasses

import pytest

from eval_runner.backends import BACKENDS
from eval_runner.cases import load_cases
from eval_runner.cli import EVALS_DIRECTORY
from eval_runner.report import LAYER_HEADING, format_report
from eval_runner.runner import run_case, run_cases
from eval_runner.scoring import summarize
from lab_health.lab_state import read_lab_state
from tests.lab.conftest import fetch_all
from tests.lab.fault_helpers import clear_and_wait_for_baseline

pytestmark = pytest.mark.lab

CASES_BY_ID = {case.case_id: case for case in load_cases(EVALS_DIRECTORY / "cases")}


def quicker(case, observe_seconds=15):
    return dataclasses.replace(case, observe_seconds=observe_seconds)


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def orders_has_its_index(connection):
    index_names = {row[2] for row in fetch_all(connection, "SHOW INDEX FROM shop.orders")}
    return "idx_orders_customer_created" in index_names


def test_rule_engine_run_is_scored_and_leaves_the_lab_at_baseline(connect_as):
    case = quicker(CASES_BY_ID["basic-dropped-index"], observe_seconds=20)

    case_result = run_case(case, BACKENDS["rule_engine"])

    assert case_result.outcome == "hit"
    assert case_result.answered_root_cause == "missing_index_slow_query"
    assert case_result.index_suggestion == ("orders", ("customer_id", "created_at"))
    assert case_result.seconds_taken > 4  # it really collected evidence over a window
    assert orders_has_its_index(connect_as("shop_diag"))
    lab_reading = read_lab_state()
    assert lab_reading.lock_waits == 0 and lab_reading.connected_sessions < 10


def test_a_backend_that_raises_is_recorded_and_the_lab_is_still_restored(connect_as):
    def broken_backend(alert_text, access):
        raise RuntimeError("model API unreachable")

    case = quicker(CASES_BY_ID["basic-dropped-index"], observe_seconds=5)

    case_result = run_case(case, broken_backend)

    assert case_result.outcome == "diagnosis_error"
    assert "model API unreachable" in case_result.invalid_reason
    assert orders_has_its_index(connect_as("shop_diag"))
    # It counts against the hit rate; it is not quietly dropped.
    assert summarize([case_result]).hit_rate.text() == "0.0% (0/1)"


def test_a_case_that_contradicts_the_injector_record_is_invalid_not_a_miss():
    wrong_case = dataclasses.replace(
        quicker(CASES_BY_ID["basic-long-transaction"], observe_seconds=5),
        expected_root_cause="connection_exhaustion",
    )

    case_result = run_case(wrong_case, BACKENDS["always_healthy"])

    assert case_result.outcome == "invalid"
    assert "lock_wait" in case_result.invalid_reason
    summary = summarize([case_result])
    assert summary.invalid_count == 1 and summary.hit_rate.text() == "n/a (0/0)"


def test_always_wrong_backends_get_the_scores_they_deserve():
    """Proof that scoring is wired to reality, on real runs."""
    cases = [
        quicker(CASES_BY_ID["basic-long-transaction"], observe_seconds=5),
        quicker(CASES_BY_ID["basic-healthy-quiet"], observe_seconds=5),
        quicker(CASES_BY_ID["hard-cascade-lock-wait"], observe_seconds=5),
        quicker(CASES_BY_ID["hard-metrics-gap-admin-port"], observe_seconds=5),
    ]

    healthy_results = run_cases(cases, BACKENDS["always_healthy"])
    exhaustion_results = run_cases(cases, BACKENDS["always_connection_exhaustion"])

    always_healthy = summarize(healthy_results)
    # Three fault cases, none hit; the healthy case is right, so no false positive.
    assert always_healthy.hit_rate.text() == "0.0% (0/3)"
    assert always_healthy.false_positive_rate.text() == "0.0% (0/1)"
    assert always_healthy.missing_metric_read_as_healthy_rate.text() == "100.0% (1/1)"

    always_exhaustion = summarize(exhaustion_results)
    # It names the symptom on the cascade, is right on the flood, and raises a
    # false alarm on the healthy lab.
    assert always_exhaustion.symptom_as_cause_rate.text() == "100.0% (1/1)"
    assert always_exhaustion.hit_rate.text() == "33.3% (1/3)"
    assert always_exhaustion.false_positive_rate.text() == "100.0% (1/1)"

    report_text = format_report(healthy_results, "always_healthy")
    assert report_text.startswith(LAYER_HEADING)
    assert "replay" not in report_text.lower()
    assert "0.0% (0/3)" in report_text


def test_a_backend_returning_an_unusable_onset_loses_the_onset_not_the_run():
    from diagnosis.contract import LOCK_WAIT, Diagnosis

    def backend_with_bad_onset(alert_text, access):
        return Diagnosis(root_cause=LOCK_WAIT, rationale="test double", onset="about ten o'clock")

    case = quicker(CASES_BY_ID["basic-long-transaction"], observe_seconds=5)

    case_result = run_case(case, backend_with_bad_onset)

    assert case_result.outcome == "hit"
    assert case_result.onset_error_seconds is None
