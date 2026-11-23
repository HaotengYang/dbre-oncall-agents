"""Builds the model-comparison table (Markdown) from replay reports.

python scripts/replay_table.py                     latest replay report per model
python scripts/replay_table.py REPORT.json ...     these reports

Replay layer only: every backend saw the same recorded evidence. Rates count
every run (a model run three times on 6 fault cases is scored out of 18).
"""

import json
import pathlib
import statistics
import sys

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTS_DIRECTORY = REPOSITORY_ROOT / "var" / "reports"
TIERS = (("basic", "Basic"), ("hard", "Hard"), ("holdout", "Holdout"), ("incident", "Incidents"))


def latest_report_per_model():
    chosen = {}
    for path in sorted(REPORTS_DIRECTORY.glob("*-replay.json")):  # oldest first, later wins
        report = json.loads(path.read_text())
        models = {row["backend"] for row in report["results"]} - {"rule_engine"}
        for model in models:
            chosen[model] = path
    return sorted(set(chosen.values()))


def rate(numerator, denominator):
    if denominator == 0:
        return "n/a"
    return f"{numerator / denominator:.0%} ({numerator}/{denominator})"


def backend_row(backend, rows):
    cells = [backend]
    for tier, _ in TIERS:
        tier_rows = [row for row in rows if row["tier"] == tier]
        faults = [row for row in tier_rows if row["expected"] != "healthy"]
        healthy = [row for row in tier_rows if row["expected"] == "healthy"]
        cells.append(rate(sum(row["outcome"] == "hit" for row in faults), len(faults)))
        cells.append(rate(sum(row["outcome"] == "false_positive" for row in healthy), len(healthy)))
    answers_by_case = {}
    for row in rows:
        answers_by_case.setdefault(row["case_id"], []).append(row["answered"])
    repeated = {case: answers for case, answers in answers_by_case.items() if len(answers) > 1}
    cells.append(rate(sum(len(set(answers)) == 1 for answers in repeated.values()), len(repeated))
                 if repeated else "1 run")
    answered = [row for row in rows if row["outcome"] != "diagnosis_error"]
    if backend == "rule_engine":
        cells.append("<0.1 s")
    else:
        cells.append(f"{statistics.mean(row['seconds'] for row in answered):.1f} s"
                     if answered else "n/a")
    cells.append(f"{sum(row['outcome'] == 'diagnosis_error' for row in rows)}")
    cells.append(f"{sum(row['cost_usd'] for row in rows):.2f} USD")
    return cells


def build_table(report_paths):
    reports = [json.loads(pathlib.Path(path).read_text()) for path in report_paths]
    rows_by_backend = {}
    for report in reports:
        for row in report["results"]:
            if row["backend"] == "rule_engine" and "rule_engine" in rows_by_backend \
                    and report is not reports[0]:
                continue  # the rule engine is deterministic; take it from one report
            rows_by_backend.setdefault(row["backend"], []).append(row)
    header = ["Backend"]
    for _, label in TIERS:
        header += [f"{label} hit", f"{label} FP"]
    header += ["Stability", "Mean time", "No answer", "Spent"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for backend, rows in rows_by_backend.items():
        lines.append("| " + " | ".join(backend_row(backend, rows)) + " |")
    stops = [report["stop_reason"] for report in reports if report.get("stop_reason")]
    return "\n".join(lines), stops


def main(arguments):
    paths = arguments or latest_report_per_model()
    table, stops = build_table(paths)
    print(table)
    for stop in stops:
        print(f"\nstopped early: {stop}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
