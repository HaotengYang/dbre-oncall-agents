"""Thresholds turn a report into a pass or a fail.

evals/thresholds.json is set by the owner and changes only with the owner's
consent. It has two parts, because a rate means little on a handful of cases:

- "full": minimum hit rate and maximum false-positive rate per tier. Applied
  only when the full case set is run. null means "not set".
- "quick": plain counts for the quick set, which has too few cases for a rate.

Any other selection of cases (a single case, for example) is not judged
against thresholds at all. A case that got no answer because the backend
failed is always a violation, whatever was run.
"""

import json

from eval_runner.scoring import summarize

TIERS = ("basic", "hard")
RATE_KEYS = ("min_hit_rate", "max_false_positive_rate")
QUICK_KEYS = ("max_false_positives", "max_misses")
# Outcomes that count as a miss for the quick set's count. "acceptable" is not
# one: it is the designed answer on thin evidence.
MISS_OUTCOMES = ("miss", "symptom_as_cause", "missing_metric_read_as_healthy")


class ThresholdError(Exception):
    """The thresholds file is malformed."""


def load_thresholds(thresholds_path):
    name = thresholds_path.name
    try:
        content = json.loads(thresholds_path.read_text())
    except ValueError as parse_error:
        raise ThresholdError(f"{name}: not valid JSON ({parse_error})") from None
    if not isinstance(content, dict) or set(content) != {"full", "quick"}:
        raise ThresholdError(f"{name}: must have exactly the parts 'full' and 'quick'")

    full = content["full"]
    if not isinstance(full, dict) or set(full) != set(TIERS):
        raise ThresholdError(f"{name}: 'full' must have exactly the tiers {list(TIERS)}")
    for tier in TIERS:
        if not isinstance(full[tier], dict) or set(full[tier]) != set(RATE_KEYS):
            raise ThresholdError(f"{name}: full.{tier} must have exactly {list(RATE_KEYS)}")
        for key in RATE_KEYS:
            value = full[tier][key]
            is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
            if value is not None and not (is_number and 0 <= value <= 1):
                raise ThresholdError(
                    f"{name}: full.{tier}.{key} must be null or between 0 and 1, got {value!r}"
                )

    quick = content["quick"]
    if not isinstance(quick, dict) or set(quick) != set(QUICK_KEYS):
        raise ThresholdError(f"{name}: 'quick' must have exactly {list(QUICK_KEYS)}")
    for key in QUICK_KEYS:
        value = quick[key]
        is_count = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        if value is not None and not is_count:
            raise ThresholdError(
                f"{name}: quick.{key} must be null or a whole number of cases, got {value!r}"
            )
    return content


def threshold_violations(results, thresholds, set_name):
    """Returns one line per threshold that the results fall short of.

    set_name is "full", "quick", or anything else for a selection that is not
    judged against thresholds.
    """
    violations = []
    no_answer_count = summarize(results).no_answer_count
    if no_answer_count:
        # Checked whatever was run. A backend that crashes on a healthy case
        # has raised no false positive, and must not pass for it.
        violations.append(f"{no_answer_count} case(s) got no answer because the backend failed")
    invalid_cases = [r.case.case_id for r in results if r.outcome == "invalid"]
    if invalid_cases and set_name in ("full", "quick"):
        # Invalid cases are left out of every rate and count. Without this,
        # each one would make the bar easier to clear: four hits and two
        # invalid cases would read as 4/4.
        violations.append(
            f"{len(invalid_cases)} case(s) could not be scored because the case and the "
            f"injector disagree: {', '.join(invalid_cases)}"
        )

    if set_name == "quick":
        false_positives = [r.case.case_id for r in results if r.outcome == "false_positive"]
        misses = [r.case.case_id for r in results if r.outcome in MISS_OUTCOMES]
        limits = thresholds["quick"]
        if limits["max_false_positives"] is not None and len(false_positives) > limits["max_false_positives"]:
            violations.append(
                f"quick set: {len(false_positives)} false positive(s), at most "
                f"{limits['max_false_positives']} allowed: {', '.join(false_positives)}"
            )
        if limits["max_misses"] is not None and len(misses) > limits["max_misses"]:
            violations.append(
                f"quick set: {len(misses)} miss(es), at most {limits['max_misses']} allowed: "
                f"{', '.join(misses)}"
            )
    elif set_name == "full":
        for tier in TIERS:
            summary = summarize(results, tier)
            limits = thresholds["full"][tier]
            minimum_hit_rate = limits["min_hit_rate"]
            if minimum_hit_rate is not None and summary.hit_rate.value is not None:
                if summary.hit_rate.value < minimum_hit_rate:
                    violations.append(
                        f"{tier}: hit rate {summary.hit_rate.text()} is below the minimum "
                        f"{100 * minimum_hit_rate:.1f}%"
                    )
            maximum_false_positive_rate = limits["max_false_positive_rate"]
            if maximum_false_positive_rate is not None and summary.false_positive_rate.value is not None:
                if summary.false_positive_rate.value > maximum_false_positive_rate:
                    violations.append(
                        f"{tier}: false-positive rate {summary.false_positive_rate.text()} is "
                        f"above the maximum {100 * maximum_false_positive_rate:.1f}%"
                    )
    return violations


def describe_what_was_checked(thresholds, set_name):
    """One line for the report, so a pass is never read as more than it is."""
    if set_name == "quick":
        limits = thresholds["quick"]
        return (
            f"Checked against the quick-set limits: at most {limits['max_false_positives']} "
            f"false positive(s) and {limits['max_misses']} miss(es)."
        )
    if set_name == "full":
        parts = [
            f"{tier}: hit rate at least {thresholds['full'][tier]['min_hit_rate']}, "
            f"false-positive rate at most {thresholds['full'][tier]['max_false_positive_rate']}"
            for tier in TIERS
        ]
        return "Checked against the full-set thresholds. " + "; ".join(parts) + "."
    return "This selection of cases is not judged against thresholds."
