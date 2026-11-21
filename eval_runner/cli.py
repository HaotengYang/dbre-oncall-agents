"""Command line: python -m eval_runner <command>."""

import argparse
import json
import pathlib
import sys

from eval_runner.backends import BACKENDS
from eval_runner.cases import CaseError, load_cases
from eval_runner.preflight import PreflightError, check_lab_accounts
from eval_runner.report import REPORTS_DIRECTORY, format_report, save_report
from eval_runner.runner import RunAborted, run_cases
from eval_runner.thresholds import ThresholdError, load_thresholds, threshold_violations

EVALS_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "evals"
EXIT_BELOW_THRESHOLD = 1
EXIT_USAGE = 2
EXIT_ABORTED = 3


def select_cases(all_cases, set_name, case_id):
    if case_id is not None:
        selected = [case for case in all_cases if case.case_id == case_id]
        if not selected:
            raise CaseError(f"no case with id '{case_id}'")
        return selected
    if set_name == "full":
        return all_cases
    sets = json.loads((EVALS_DIRECTORY / "sets.json").read_text())
    if set_name not in sets:
        raise CaseError(f"unknown set '{set_name}'. Available: full, {', '.join(sorted(sets))}")
    by_id = {case.case_id: case for case in all_cases}
    unknown_ids = [listed_id for listed_id in sets[set_name] if listed_id not in by_id]
    if unknown_ids:
        raise CaseError(f"set '{set_name}' names unknown cases: {unknown_ids}")
    return [by_id[listed_id] for listed_id in sets[set_name]]


def run_command(arguments):
    all_cases = load_cases(EVALS_DIRECTORY / "cases")
    cases = select_cases(all_cases, arguments.set, arguments.case)
    thresholds = load_thresholds(EVALS_DIRECTORY / "thresholds.json")
    check_lab_accounts()
    print(f"running {len(cases)} case(s) with backend '{arguments.backend}'", flush=True)

    def show_progress(case_result):
        print(f"  {case_result.case.case_id}: expected {case_result.case.expected_root_cause}, "
              f"answered {case_result.answered_root_cause} -> {case_result.outcome}", flush=True)

    aborted_reason = None
    try:
        results = run_cases(cases, BACKENDS[arguments.backend], on_result=show_progress)
    except RunAborted as aborted:
        # Keep what was scored: a partial report beats one line on stderr.
        results, aborted_reason = aborted.results, str(aborted)
    report_path = save_report(results, arguments.backend, aborted_reason=aborted_reason)
    print()
    print(format_report(results, arguments.backend, aborted_reason))
    print(f"report saved to {report_path}")
    if aborted_reason:
        print(f"run aborted, lab not at baseline: {aborted_reason}", file=sys.stderr)
        return EXIT_ABORTED

    violations = threshold_violations(results, thresholds)
    if violations:
        print("\nBELOW THRESHOLD")
        for violation in violations:
            print(f"  {violation}")
        return EXIT_BELOW_THRESHOLD
    if all(value is None for tier in thresholds.values() for value in tier.values()):
        print("\nNo thresholds are set in evals/thresholds.json; nothing was checked.")
    return 0


def report_command(arguments):
    latest_path = REPORTS_DIRECTORY / "latest.json"
    if not latest_path.exists():
        print("no report yet; run: python -m eval_runner run --set quick", file=sys.stderr)
        return EXIT_USAGE
    print(json.loads(latest_path.read_text())["text"])
    return 0


def main(argument_list=None):
    parser = argparse.ArgumentParser(
        prog="python -m eval_runner",
        description="Score a diagnosis backend against the eval cases, on the live lab.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run cases and print the report")
    run.add_argument("--set", default="quick", help="'quick' (default), 'full', or a set in evals/sets.json")
    run.add_argument("--case", default=None, help="run a single case by id")
    run.add_argument("--backend", default="rule_engine", choices=sorted(BACKENDS))
    commands.add_parser("report", help="print the last report again")
    arguments = parser.parse_args(argument_list)
    try:
        return {"run": run_command, "report": report_command}[arguments.command](arguments)
    except (CaseError, ThresholdError, PreflightError) as problem:
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_USAGE
