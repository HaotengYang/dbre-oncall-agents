"""Turns answers into outcomes, and outcomes into rates.

The rules are those approved in docs/DESIGN.md section 7 and
docs/proposals/B-evals.md section 4. Every rate keeps its numerator and
denominator, so a report can always show "70.0% (7/10)" and not a bare
percentage.
"""

import statistics
from dataclasses import dataclass
from typing import Optional

from eval_runner.cases import EvalCase

HEALTHY = "healthy"
UNDETERMINED = "undetermined"
HEALTHY_KINDS = ("healthy", "healthy_lookalike")


@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int

    @property
    def value(self):
        return None if self.denominator == 0 else self.numerator / self.denominator

    def text(self):
        if self.denominator == 0:
            return f"n/a ({self.numerator}/{self.denominator})"
        return f"{100 * self.numerator / self.denominator:.1f}% ({self.numerator}/{self.denominator})"


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    answered_root_cause: str
    answered_symptoms: tuple
    outcome: str
    index_suggestion: Optional[tuple]  # (table, (column, ...)) or None
    onset_error_seconds: Optional[float]  # reported onset minus the injector's start
    seconds_taken: float
    invalid_reason: Optional[str]


def classify_outcome(case, answered_root_cause):
    """Returns exactly one outcome for an answer."""
    if case.kind == "metrics_gap" and answered_root_cause == HEALTHY:
        return "missing_metric_read_as_healthy"
    if answered_root_cause == case.expected_root_cause:
        return "hit"
    if answered_root_cause in case.also_acceptable:
        return "acceptable"
    if case.expected_root_cause == HEALTHY:
        return "undetermined_on_healthy" if answered_root_cause == UNDETERMINED else "false_positive"
    if case.kind == "cascade" and answered_root_cause in case.expected_symptoms:
        return "symptom_as_cause"
    return "miss"


def index_suggestion_is_correct(expected_index, index_suggestion):
    """Right table, and the expected columns lead the suggested index.

    Extra trailing columns are accepted: an index on (a, b, c) serves a query
    that needs (a, b).
    """
    if index_suggestion is None:
        return False
    expected_table, expected_columns = expected_index
    suggested_table, suggested_columns = index_suggestion
    return (
        suggested_table == expected_table
        and tuple(suggested_columns[: len(expected_columns)]) == tuple(expected_columns)
    )


@dataclass(frozen=True)
class Summary:
    case_count: int
    invalid_count: int
    # Cases where the backend raised instead of answering. They count against
    # the hit rate, and any of them fails the threshold check outright.
    no_answer_count: int
    acceptable_count: int
    hit_rate: Rate
    false_positive_rate: Rate
    symptom_as_cause_rate: Rate
    undetermined_rate: Rate
    undetermined_on_healthy_rate: Rate
    missing_metric_read_as_healthy_rate: Rate
    symptoms_listed_rate: Rate
    index_suggestion_rate: Rate
    median_onset_error_seconds: Optional[float]


def summarize(results, tier=None):
    """Computes the rates over valid results, optionally for one tier."""
    selected = [r for r in results if tier is None or r.case.tier == tier]
    valid = [r for r in selected if r.outcome != "invalid"]

    def count(outcome):
        return sum(1 for r in valid if r.outcome == outcome)

    fault_cases = [r for r in valid if r.case.expected_root_cause != HEALTHY]
    healthy_cases = [r for r in valid if r.case.expected_root_cause == HEALTHY]
    cascade_cases = [r for r in valid if r.case.kind == "cascade"]
    cascade_hits = [r for r in cascade_cases if r.outcome == "hit"]
    metrics_gap_cases = [r for r in valid if r.case.kind == "metrics_gap"]
    index_cases = [r for r in valid if r.case.expected_index is not None]
    onset_errors = [abs(r.onset_error_seconds) for r in valid if r.onset_error_seconds is not None]

    return Summary(
        case_count=len(valid),
        invalid_count=len(selected) - len(valid),
        no_answer_count=count("diagnosis_error"),
        acceptable_count=count("acceptable"),
        hit_rate=Rate(sum(1 for r in fault_cases if r.outcome == "hit"), len(fault_cases)),
        false_positive_rate=Rate(count("false_positive"), len(healthy_cases)),
        symptom_as_cause_rate=Rate(count("symptom_as_cause"), len(cascade_cases)),
        undetermined_rate=Rate(
            sum(1 for r in valid if r.answered_root_cause == UNDETERMINED), len(valid)
        ),
        undetermined_on_healthy_rate=Rate(count("undetermined_on_healthy"), len(healthy_cases)),
        missing_metric_read_as_healthy_rate=Rate(
            count("missing_metric_read_as_healthy"), len(metrics_gap_cases)
        ),
        symptoms_listed_rate=Rate(
            sum(1 for r in cascade_hits
                if set(r.case.expected_symptoms) <= set(r.answered_symptoms)),
            len(cascade_hits),
        ),
        index_suggestion_rate=Rate(
            sum(1 for r in index_cases
                if index_suggestion_is_correct(r.case.expected_index, r.index_suggestion)),
            len(index_cases),
        ),
        median_onset_error_seconds=statistics.median(onset_errors) if onset_errors else None,
    )
