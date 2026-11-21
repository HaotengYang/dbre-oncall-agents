"""Runs eval cases against the live lab (the end-to-end layer)."""

import datetime
import time

from diagnosis.access import load_diagnosis_access
from diagnosis.contract import ROOT_CAUSES, Diagnosis
from eval_runner.scoring import CaseResult, classify_outcome
from fault_injector.engine import injected_fault
from lab_health.baseline_gate import BaselineNotRestored
from lab_health.lab_state import wait_for_lab_baseline


class RunAborted(Exception):
    """The lab did not return to baseline. Later cases would be contaminated.

    results holds what was scored before the abort, so a partial report can
    still be produced.
    """

    def __init__(self, message, results=()):
        super().__init__(message)
        self.results = list(results)


def _wait_for_baseline(case_id, moment):
    try:
        wait_for_lab_baseline()
    except BaselineNotRestored as not_restored:
        raise RunAborted(f"{moment} case {case_id}: {not_restored}") from not_restored


def _seconds_between(earlier_iso, later_iso):
    """Seconds from earlier to later, or None if either cannot be compared.

    The later value comes from a diagnosis backend. A model can return
    anything there; an unusable onset loses the onset figure, not the run.
    """
    try:
        earlier = datetime.datetime.fromisoformat(earlier_iso)
        later = datetime.datetime.fromisoformat(later_iso)
        return (later - earlier).total_seconds()
    except (TypeError, ValueError):
        return None


def _consistency_problem(case, injection):
    """Returns why the case and the injector's own record disagree, or None.

    A disagreement means the case file or the injector is wrong. Such a case
    is reported as invalid; it says nothing about the diagnosis.
    """
    if injection is None:
        return None
    if injection["root_cause"] != case.expected_root_cause:
        return (
            f"injector recorded root cause {injection['root_cause']!r}, "
            f"case expects {case.expected_root_cause!r}"
        )
    if set(injection["expected_symptoms"]) != set(case.expected_symptoms):
        return (
            f"injector recorded symptoms {sorted(injection['expected_symptoms'])}, "
            f"case expects {sorted(case.expected_symptoms)}"
        )
    return None


def _answer_problem(diagnosis):
    """Returns why a backend's return value cannot be scored, or None.

    A model backend can return anything. Everything read from the answer later
    is checked here, so a malformed answer becomes a "no answer" for its case
    instead of an exception that ends the run.
    """
    if not isinstance(diagnosis, Diagnosis):
        return f"backend returned {type(diagnosis).__name__}, not a Diagnosis"
    if diagnosis.root_cause not in ROOT_CAUSES:
        return f"unknown root cause {diagnosis.root_cause!r}"
    symptoms = diagnosis.symptoms
    if not isinstance(symptoms, (tuple, list)) or not all(isinstance(s, str) for s in symptoms):
        return f"symptoms must be a list of labels, got {symptoms!r}"
    suggestion = diagnosis.index_suggestion
    if suggestion is not None:
        columns = getattr(suggestion, "columns", None)
        table = getattr(suggestion, "table", None)
        if not isinstance(table, str) or not isinstance(columns, (tuple, list)) or not all(
            isinstance(column, str) for column in columns
        ):
            return f"index suggestion must have a table and a list of columns, got {suggestion!r}"
    return None


def run_case(case, diagnose, sleep=time.sleep):
    """Runs one case end to end and returns its CaseResult.

    The lab is at baseline before the scenario starts and again when this
    returns, also when the diagnosis function raises.
    """
    _wait_for_baseline(case.case_id, "before")
    access = load_diagnosis_access(admin_port_available=case.admin_port_available)
    injection = None
    diagnosis = None
    diagnosis_error = None
    seconds_taken = 0.0

    def observe_then_diagnose():
        nonlocal diagnosis, diagnosis_error, seconds_taken
        sleep(case.observe_seconds)
        started_at = time.monotonic()
        try:
            diagnosis = diagnose(case.alert, access)
        except Exception as raised:  # a failing backend must not end the run
            diagnosis_error = f"{type(raised).__name__}: {raised}"
        seconds_taken = time.monotonic() - started_at
        if diagnosis_error is None:
            diagnosis_error = _answer_problem(diagnosis)
            if diagnosis_error is not None:
                diagnosis = None

    if case.scenario is None:
        observe_then_diagnose()
    else:
        with injected_fault(case.scenario) as active_injection:
            injection = active_injection
            observe_then_diagnose()
    _wait_for_baseline(case.case_id, "after")

    invalid_reason = _consistency_problem(case, injection)
    if diagnosis is None:
        return CaseResult(
            case=case, answered_root_cause="(no answer)", answered_symptoms=(),
            outcome="invalid" if invalid_reason else "diagnosis_error",
            index_suggestion=None, onset_error_seconds=None, seconds_taken=seconds_taken,
            invalid_reason=invalid_reason or diagnosis_error,
        )

    onset_error_seconds = None
    if injection is not None and diagnosis.onset:
        onset_error_seconds = _seconds_between(injection["started_at"], diagnosis.onset)
    suggestion = diagnosis.index_suggestion
    return CaseResult(
        case=case,
        answered_root_cause=diagnosis.root_cause,
        answered_symptoms=tuple(diagnosis.symptoms),
        outcome="invalid" if invalid_reason else classify_outcome(case, diagnosis.root_cause),
        index_suggestion=(suggestion.table, tuple(suggestion.columns)) if suggestion else None,
        onset_error_seconds=onset_error_seconds,
        seconds_taken=seconds_taken,
        invalid_reason=invalid_reason,
    )


def run_cases(cases, diagnose, on_result=None):
    """Runs cases in order. Stops with RunAborted if the lab does not recover."""
    results = []
    for case in cases:
        try:
            case_result = run_case(case, diagnose)
        except RunAborted as aborted:
            raise RunAborted(str(aborted), results) from aborted
        except Exception as unexpected:
            # For example the injector refusing to start, or the database
            # going away. The lab's state is unknown, so stop, but keep what
            # was scored.
            raise RunAborted(
                f"case {case.case_id}: {type(unexpected).__name__}: {unexpected}", results
            ) from unexpected
        results.append(case_result)
        if on_result is not None:
            on_result(case_result)
    return results
