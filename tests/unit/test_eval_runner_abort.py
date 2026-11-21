"""A run that has to stop keeps what it scored."""

import pytest

from eval_runner import runner
from eval_runner.report import format_report
from eval_runner.scoring import CaseResult
from tests.unit.test_eval_scoring import make_case


def test_abort_carries_the_results_scored_so_far(monkeypatch):
    cases = [make_case("first"), make_case("second"), make_case("third")]

    def fake_run_case(case, diagnose):
        if case.case_id == "second":
            raise runner.RunAborted("after case second: lab not healthy after 120 s")
        return CaseResult(case, "lock_wait", (), "hit", None, None, 1.0, None)

    monkeypatch.setattr(runner, "run_case", fake_run_case)

    with pytest.raises(runner.RunAborted) as aborted:
        runner.run_cases(cases, diagnose=None)

    assert [r.case.case_id for r in aborted.value.results] == ["first"]
    report_text = format_report(aborted.value.results, "rule_engine", str(aborted.value))
    assert "RUN ABORTED" in report_text and "lab not healthy" in report_text
    assert "100.0% (1/1)" in report_text  # the scored case is still reported


def test_seconds_between_tolerates_what_a_model_might_return():
    started = "2026-11-21T10:00:00.000+00:00"

    assert runner._seconds_between(started, "2026-11-21T10:00:14+00:00") == 14
    assert runner._seconds_between(started, "2026-11-21T10:00:05") is None  # no offset
    assert runner._seconds_between(started, "about 10:00") is None
    assert runner._seconds_between(started, None) is None


def test_an_unexpected_error_while_running_a_case_also_keeps_earlier_results(monkeypatch):
    cases = [make_case("first"), make_case("second")]

    def fake_run_case(case, diagnose):
        if case.case_id == "second":
            raise ConnectionError("database went away")
        return CaseResult(case, "lock_wait", (), "hit", None, None, 1.0, None)

    monkeypatch.setattr(runner, "run_case", fake_run_case)

    with pytest.raises(runner.RunAborted) as aborted:
        runner.run_cases(cases, diagnose=None)

    assert [r.case.case_id for r in aborted.value.results] == ["first"]
    assert "second" in str(aborted.value) and "database went away" in str(aborted.value)


def test_malformed_answers_are_described_and_good_ones_pass():
    from diagnosis.contract import Diagnosis, IndexSuggestion

    good = Diagnosis(root_cause="lock_wait", rationale="because",
                     symptoms=("connection_exhaustion",),
                     index_suggestion=IndexSuggestion("orders", ("customer_id",)))

    assert runner._answer_problem(good) is None
    assert "not a Diagnosis" in runner._answer_problem({"root_cause": "lock_wait"})
    assert "not a Diagnosis" in runner._answer_problem(None)
    assert "symptoms" in runner._answer_problem(
        Diagnosis(root_cause="lock_wait", rationale="x", symptoms=None))
    assert "symptoms" in runner._answer_problem(
        Diagnosis(root_cause="lock_wait", rationale="x", symptoms="connection_exhaustion"))
    assert "index suggestion" in runner._answer_problem(
        Diagnosis(root_cause="lock_wait", rationale="x",
                  index_suggestion={"table": "orders", "columns": ["customer_id"]}))


def test_report_lists_cases_without_an_answer_and_says_the_run_fails():
    crashed = CaseResult(make_case("crashed"), "(no answer)", (), "diagnosis_error", None,
                         None, 0.1, "RuntimeError: model API unreachable")

    report_text = format_report([crashed], "some_backend")

    assert "NO ANSWER" in report_text and "the run fails" in report_text
    assert "crashed: RuntimeError: model API unreachable" in report_text
