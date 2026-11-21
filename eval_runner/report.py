"""Formats eval results for the terminal and stores them as JSON."""

import datetime
import json
import pathlib

from eval_runner.scoring import summarize

REPORTS_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "var" / "reports"
LAYER_HEADING = "END-TO-END (live lab, whole chain: evidence collection and diagnosis)"


def _table(rows):
    widths = [max(len(str(row[column])) for row in rows) for column in range(len(rows[0]))]
    lines = []
    for position, row in enumerate(rows):
        lines.append("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)).rstrip())
        if position == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def _rate_lines(summary):
    onset = summary.median_onset_error_seconds
    return [
        ("Hit rate", summary.hit_rate.text(), "hits / cases whose expected answer is a fault"),
        ("False-positive rate", summary.false_positive_rate.text(),
         "healthy cases answered with a fault"),
        ("Symptom-as-cause rate", summary.symptom_as_cause_rate.text(),
         "cascades answered with the symptom"),
        ("Undetermined rate", summary.undetermined_rate.text(), "answers of 'undetermined'"),
        ("Undetermined on healthy", summary.undetermined_on_healthy_rate.text(),
         "mild false alarms"),
        ("Missing metric read as healthy", summary.missing_metric_read_as_healthy_rate.text(),
         "metrics-gap cases answered 'healthy'"),
        ("Symptoms listed", summary.symptoms_listed_rate.text(),
         "cascade hits that also named the symptom"),
        ("Index suggestion correct", summary.index_suggestion_rate.text(),
         "does not affect hits"),
        ("Acceptable, not hits", f"{summary.acceptable_count}", "in practice 'undetermined' on thin evidence"),
        ("No answer", f"{summary.no_answer_count}", "backend failed; any of these fails the run"),
        ("Median onset error", "n/a" if onset is None else f"{onset:.0f} s", "reported, not scored"),
    ]


def format_report(results, backend_name, aborted_reason=None):
    sections = [LAYER_HEADING, f"backend: {backend_name}", ""]
    for tier in ("basic", "hard"):
        tier_results = [r for r in results if r.case.tier == tier]
        if not tier_results:
            continue
        rows = [("case", "kind", "expected", "answered", "outcome", "seconds")]
        for r in tier_results:
            rows.append((r.case.case_id, r.case.kind, r.case.expected_root_cause,
                         r.answered_root_cause, r.outcome, f"{r.seconds_taken:.1f}"))
        sections += [f"{tier.upper()} TIER", _table(rows), ""]
        sections += [_table([("number", "value", "meaning")] + _rate_lines(summarize(results, tier))), ""]

    sections += ["ALL CASES", _table([("number", "value", "meaning")] + _rate_lines(summarize(results))), ""]
    no_answer = [r for r in results if r.outcome == "diagnosis_error"]
    if no_answer:
        sections.append("NO ANSWER (the backend failed; counted against the hit rate, "
                        "and the run fails)")
        sections += [f"  {r.case.case_id}: {r.invalid_reason}" for r in no_answer]
        sections.append("")
    invalid = [r for r in results if r.outcome == "invalid"]
    if invalid:
        sections.append("INVALID CASES (case and injector disagree; not scored)")
        sections += [f"  {r.case.case_id}: {r.invalid_reason}" for r in invalid]
        sections.append("")
    if aborted_reason:
        sections.append("RUN ABORTED (the lab did not return to baseline; later cases not run)")
        sections += [f"  {aborted_reason}", ""]
    return "\n".join(sections)


def results_as_json(results, backend_name, aborted_reason=None):
    def rate(value):
        return {"numerator": value.numerator, "denominator": value.denominator, "text": value.text()}

    def summary_json(summary):
        return {
            "hit_rate": rate(summary.hit_rate),
            "false_positive_rate": rate(summary.false_positive_rate),
            "symptom_as_cause_rate": rate(summary.symptom_as_cause_rate),
            "undetermined_rate": rate(summary.undetermined_rate),
            "undetermined_on_healthy_rate": rate(summary.undetermined_on_healthy_rate),
            "missing_metric_read_as_healthy_rate": rate(summary.missing_metric_read_as_healthy_rate),
            "symptoms_listed_rate": rate(summary.symptoms_listed_rate),
            "index_suggestion_rate": rate(summary.index_suggestion_rate),
            "acceptable_count": summary.acceptable_count,
            "invalid_count": summary.invalid_count,
            "no_answer_count": summary.no_answer_count,
            "median_onset_error_seconds": summary.median_onset_error_seconds,
        }

    return {
        "layer": "end_to_end",
        "backend": backend_name,
        "aborted_reason": aborted_reason,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "cases": [
            {
                "id": r.case.case_id, "tier": r.case.tier, "kind": r.case.kind,
                "expected": r.case.expected_root_cause, "answered": r.answered_root_cause,
                "answered_symptoms": list(r.answered_symptoms), "outcome": r.outcome,
                "index_suggestion": list(r.index_suggestion) if r.index_suggestion else None,
                "onset_error_seconds": r.onset_error_seconds,
                "seconds_taken": round(r.seconds_taken, 2), "note": r.invalid_reason,
            }
            for r in results
        ],
        "summary": {
            "basic": summary_json(summarize(results, "basic")),
            "hard": summary_json(summarize(results, "hard")),
            "all": summary_json(summarize(results)),
        },
    }


def save_report(results, backend_name, reports_directory=REPORTS_DIRECTORY, aborted_reason=None):
    reports_directory.mkdir(parents=True, exist_ok=True)
    report = results_as_json(results, backend_name, aborted_reason)
    report["text"] = format_report(results, backend_name, aborted_reason)
    stamp = report["created_at"].replace(":", "").replace("+0000", "Z")
    report_path = reports_directory / f"{stamp}-{backend_name}.json"
    report_path.write_text(json.dumps(report, indent=2))
    (reports_directory / "latest.json").write_text(json.dumps(report, indent=2))
    return report_path
