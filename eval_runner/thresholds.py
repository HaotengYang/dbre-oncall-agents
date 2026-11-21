"""Thresholds turn a report into a pass or a fail.

evals/thresholds.json is set by the owner. A value of null means "not set
yet": nothing is checked for it.
"""

import json

from eval_runner.scoring import summarize

TIERS = ("basic", "hard")
KEYS = ("min_hit_rate", "max_false_positive_rate")


class ThresholdError(Exception):
    """The thresholds file is malformed."""


def load_thresholds(thresholds_path):
    try:
        content = json.loads(thresholds_path.read_text())
    except ValueError as parse_error:
        raise ThresholdError(f"{thresholds_path.name}: not valid JSON ({parse_error})") from None
    if not isinstance(content, dict) or set(content) != set(TIERS):
        raise ThresholdError(f"{thresholds_path.name}: must have exactly the tiers {list(TIERS)}")
    for tier in TIERS:
        if not isinstance(content[tier], dict) or set(content[tier]) != set(KEYS):
            raise ThresholdError(f"{thresholds_path.name}: tier '{tier}' must have exactly {list(KEYS)}")
        for key in KEYS:
            value = content[tier][key]
            is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
            if value is not None and not (is_number and 0 <= value <= 1):
                raise ThresholdError(
                    f"{thresholds_path.name}: {tier}.{key} must be null or between 0 and 1, "
                    f"got {value!r}"
                )
    return content


def threshold_violations(results, thresholds):
    """Returns one line per threshold that the results fall short of.

    A tier with no scored cases in this run is skipped: a threshold cannot be
    judged on zero cases.
    """
    violations = []
    no_answer_count = summarize(results).no_answer_count
    if no_answer_count:
        # Checked even when no thresholds are set. A backend that crashes on a
        # healthy case has raised no false positive, and must not pass for it.
        violations.append(
            f"{no_answer_count} case(s) got no answer because the backend failed"
        )
    for tier in TIERS:
        summary = summarize(results, tier)
        minimum_hit_rate = thresholds[tier]["min_hit_rate"]
        if minimum_hit_rate is not None and summary.hit_rate.value is not None:
            if summary.hit_rate.value < minimum_hit_rate:
                violations.append(
                    f"{tier}: hit rate {summary.hit_rate.text()} is below the minimum "
                    f"{100 * minimum_hit_rate:.1f}%"
                )
        maximum_false_positive_rate = thresholds[tier]["max_false_positive_rate"]
        if maximum_false_positive_rate is not None and summary.false_positive_rate.value is not None:
            if summary.false_positive_rate.value > maximum_false_positive_rate:
                violations.append(
                    f"{tier}: false-positive rate {summary.false_positive_rate.text()} is above "
                    f"the maximum {100 * maximum_false_positive_rate:.1f}%"
                )
    return violations
