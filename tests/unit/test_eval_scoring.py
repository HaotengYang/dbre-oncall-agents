"""Scoring turns answers into outcomes and outcomes into the numbers every
later change is judged by. Each outcome and each rate is checked against a
value worked out by hand."""

import pytest

from eval_runner.cases import EvalCase
from eval_runner.scoring import CaseResult, Rate, classify_outcome, summarize


def make_case(case_id="case", tier="basic", kind="single_fault", expected="lock_wait",
              also_acceptable=(), symptoms=(), expected_index=None):
    return EvalCase(
        case_id=case_id, tier=tier, kind=kind,
        scenario=None if kind == "healthy" else "some.scenario",
        observe_seconds=30, admin_port_available=True, alert="alert text",
        expected_root_cause=expected, also_acceptable=tuple(also_acceptable),
        expected_symptoms=tuple(symptoms), expected_index=expected_index,
    )


@pytest.mark.parametrize(
    "case, answer, expected_outcome",
    [
        (make_case(), "lock_wait", "hit"),
        (make_case(), "connection_exhaustion", "miss"),
        (make_case(), "healthy", "miss"),
        (make_case(), "undetermined", "miss"),
        (make_case(kind="incomplete_evidence", also_acceptable=["undetermined"]),
         "undetermined", "acceptable"),
        (make_case(kind="incomplete_evidence", also_acceptable=["undetermined"]),
         "lock_wait", "hit"),
        (make_case(kind="incomplete_evidence", also_acceptable=["undetermined"]),
         "healthy", "miss"),
        (make_case(kind="cascade", symptoms=["connection_exhaustion"]),
         "connection_exhaustion", "symptom_as_cause"),
        (make_case(kind="cascade", symptoms=["connection_exhaustion"]), "lock_wait", "hit"),
        (make_case(kind="cascade", symptoms=["connection_exhaustion"]),
         "missing_index_slow_query", "miss"),
        (make_case(kind="healthy", expected="healthy"), "healthy", "hit"),
        (make_case(kind="healthy", expected="healthy"), "lock_wait", "false_positive"),
        (make_case(kind="healthy_lookalike", expected="healthy"),
         "missing_index_slow_query", "false_positive"),
        (make_case(kind="healthy", expected="healthy"), "undetermined",
         "undetermined_on_healthy"),
        (make_case(kind="metrics_gap", expected="connection_exhaustion",
                   also_acceptable=["undetermined"]), "healthy",
         "missing_metric_read_as_healthy"),
        (make_case(kind="metrics_gap", expected="connection_exhaustion",
                   also_acceptable=["undetermined"]), "undetermined", "acceptable"),
        (make_case(kind="metrics_gap", expected="connection_exhaustion"),
         "connection_exhaustion", "hit"),
    ],
)
def test_each_answer_gets_exactly_one_outcome(case, answer, expected_outcome):
    assert classify_outcome(case, answer) == expected_outcome


def result(case, answer, symptoms=(), index_suggestion=None, onset_error=None, seconds=1.0):
    return CaseResult(
        case=case, answered_root_cause=answer, answered_symptoms=tuple(symptoms),
        outcome=classify_outcome(case, answer), index_suggestion=index_suggestion,
        onset_error_seconds=onset_error, seconds_taken=seconds, invalid_reason=None,
    )


def test_rate_keeps_the_fraction_and_formats_both():
    assert Rate(7, 10).text() == "70.0% (7/10)"
    assert Rate(0, 3).text() == "0.0% (0/3)"
    assert Rate(0, 0).text() == "n/a (0/0)"
    assert Rate(1, 3).value == pytest.approx(1 / 3)
    assert Rate(0, 0).value is None


def test_rates_match_hand_computed_values():
    cascade = make_case("c", kind="cascade", symptoms=["connection_exhaustion"])
    results = [
        result(make_case("f1"), "lock_wait"),                       # hit
        result(make_case("f2"), "connection_exhaustion"),           # miss
        result(make_case("f3"), "undetermined"),                    # miss, undetermined
        result(cascade, "connection_exhaustion"),                   # symptom as cause
        result(make_case("c2", kind="cascade", symptoms=["connection_exhaustion"]),
               "lock_wait", symptoms=["connection_exhaustion"]),    # hit, symptom listed
        result(make_case("h1", kind="healthy", expected="healthy"), "healthy"),        # hit
        result(make_case("h2", kind="healthy", expected="healthy"), "lock_wait"),      # FP
        result(make_case("h3", kind="healthy_lookalike", expected="healthy"),
               "undetermined"),                                     # undetermined on healthy
    ]

    summary = summarize(results)

    # Fault cases: f1 f2 f3 c c2 = 5; hits f1 and c2 = 2.
    assert (summary.hit_rate.numerator, summary.hit_rate.denominator) == (2, 5)
    # Healthy cases: h1 h2 h3 = 3; one names a fault.
    assert (summary.false_positive_rate.numerator,
            summary.false_positive_rate.denominator) == (1, 3)
    assert (summary.undetermined_on_healthy_rate.numerator,
            summary.undetermined_on_healthy_rate.denominator) == (1, 3)
    # Cascades: 2; one answered with the symptom.
    assert (summary.symptom_as_cause_rate.numerator,
            summary.symptom_as_cause_rate.denominator) == (1, 2)
    # Undetermined answers: f3 and h3, out of all 8.
    assert (summary.undetermined_rate.numerator,
            summary.undetermined_rate.denominator) == (2, 8)
    # Cascade hits: 1 (c2), and it listed the symptom.
    assert (summary.symptoms_listed_rate.numerator,
            summary.symptoms_listed_rate.denominator) == (1, 1)


def test_always_undetermined_scores_zero_hits_and_full_undetermined_rate():
    results = [
        result(make_case("f1"), "undetermined"),
        result(make_case("f2", kind="incomplete_evidence", also_acceptable=["undetermined"]),
               "undetermined"),
        result(make_case("h1", kind="healthy", expected="healthy"), "undetermined"),
    ]

    summary = summarize(results)

    assert summary.hit_rate.text() == "0.0% (0/2)"
    assert summary.undetermined_rate.text() == "100.0% (3/3)"
    assert summary.acceptable_count == 1


def test_index_suggestion_is_scored_separately_and_never_changes_the_outcome():
    index_case = make_case("i1", expected="missing_index_slow_query",
                           expected_index=("orders", ("customer_id", "created_at")))
    other_index_case = make_case("i2", expected="missing_index_slow_query",
                                 expected_index=("order_items", ("product_id",)))
    results = [
        # Right cause, right index.
        result(index_case, "missing_index_slow_query",
               index_suggestion=("orders", ("customer_id", "created_at"))),
        # Right cause, extra trailing column is fine: the expected columns lead.
        result(other_index_case, "missing_index_slow_query",
               index_suggestion=("order_items", ("product_id", "quantity"))),
        # Right cause, wrong leading column.
        result(make_case("i3", expected="missing_index_slow_query",
                         expected_index=("orders", ("customer_id", "created_at"))),
               "missing_index_slow_query", index_suggestion=("orders", ("created_at",))),
        # Right cause, no suggestion at all.
        result(make_case("i4", expected="missing_index_slow_query",
                         expected_index=("orders", ("customer_id",))),
               "missing_index_slow_query"),
        # Wrong cause but the suggestion happens to be right: still counted
        # here, because index advice is scored on its own.
        result(make_case("i5", expected="missing_index_slow_query",
                         expected_index=("orders", ("customer_id",))),
               "lock_wait", index_suggestion=("orders", ("customer_id",))),
        # A case with no expected index is not part of this rate.
        result(make_case("f1"), "lock_wait"),
    ]

    summary = summarize(results)

    assert (summary.index_suggestion_rate.numerator,
            summary.index_suggestion_rate.denominator) == (3, 5)
    assert [r.outcome for r in results] == ["hit", "hit", "hit", "hit", "miss", "hit"]


def test_invalid_cases_are_excluded_from_every_rate_and_counted_separately():
    valid = result(make_case("f1"), "lock_wait")
    invalid = CaseResult(
        case=make_case("f2"), answered_root_cause="healthy", answered_symptoms=(),
        outcome="invalid", index_suggestion=None, onset_error_seconds=None,
        seconds_taken=1.0, invalid_reason="record says lock_wait, case says healthy",
    )

    summary = summarize([valid, invalid])

    assert summary.hit_rate.text() == "100.0% (1/1)"
    assert summary.invalid_count == 1


def test_onset_error_is_the_median_of_absolute_errors_and_ignores_missing_values():
    results = [
        result(make_case("f1"), "lock_wait", onset_error=-4.0),
        result(make_case("f2"), "lock_wait", onset_error=10.0),
        result(make_case("f3"), "lock_wait", onset_error=30.0),
        result(make_case("f4"), "lock_wait", onset_error=None),
    ]

    assert summarize(results).median_onset_error_seconds == pytest.approx(10.0)
    assert summarize([result(make_case("f4"), "lock_wait")]).median_onset_error_seconds is None


def test_summaries_can_be_taken_per_tier():
    results = [
        result(make_case("b1", tier="basic"), "lock_wait"),
        result(make_case("h1", tier="hard"), "healthy"),
        result(make_case("h2", tier="hard"), "lock_wait"),
    ]

    assert summarize(results, tier="basic").hit_rate.text() == "100.0% (1/1)"
    assert summarize(results, tier="hard").hit_rate.text() == "50.0% (1/2)"


def no_answer(case):
    return CaseResult(
        case=case, answered_root_cause="(no answer)", answered_symptoms=(),
        outcome="diagnosis_error", index_suggestion=None, onset_error_seconds=None,
        seconds_taken=0.1, invalid_reason="RuntimeError: backend failed",
    )


def test_a_backend_that_fails_counts_against_the_hit_rate_and_is_counted():
    results = [
        result(make_case("f1"), "lock_wait"),
        no_answer(make_case("f2")),
        no_answer(make_case("h1", kind="healthy", expected="healthy")),
    ]

    summary = summarize(results)

    assert summary.hit_rate.text() == "50.0% (1/2)"
    assert summary.no_answer_count == 2
    # The crash on the healthy case is not a false positive, which is exactly
    # why it must fail the run some other way (see the thresholds test).
    assert summary.false_positive_rate.text() == "0.0% (0/1)"
