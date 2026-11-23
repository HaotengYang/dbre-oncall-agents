"""Command line: python -m eval_runner <command>."""

import argparse
import datetime
import json
import pathlib
import sys

from eval_runner.backends import BACKENDS
from eval_runner.cases import CaseError, load_cases
from eval_runner.preflight import PreflightError, check_lab_accounts
from eval_runner.report import REPORTS_DIRECTORY, format_report, save_report
from eval_runner.runner import RunAborted, run_cases
from eval_runner.thresholds import (
    ThresholdError,
    describe_what_was_checked,
    load_thresholds,
    threshold_violations,
)

EVALS_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "evals"
EXIT_BELOW_THRESHOLD = 1
EXIT_USAGE = 2
EXIT_ABORTED = 3


HOLDOUT_DIRECTORY_NAME = "holdout"


def load_holdout_cases():
    """The holdout cases: their own directory, never part of the full set."""
    cases = load_cases(EVALS_DIRECTORY / HOLDOUT_DIRECTORY_NAME)
    for case in cases:
        if case.tier != "holdout":
            raise CaseError(f"case '{case.case_id}' is in the holdout directory but not tier holdout")
    return cases


INCIDENT_DIRECTORY_NAME = "incident-replays"


def load_incident_cases():
    """Replays of public incidents: their own directory, never in the full set."""
    cases = load_cases(EVALS_DIRECTORY / INCIDENT_DIRECTORY_NAME)
    for case in cases:
        if case.tier != "incident":
            raise CaseError(f"case '{case.case_id}' is in incident-replays but not tier incident")
    return cases


def select_cases(all_cases, set_name, case_id):
    if set_name == "incidents":
        all_cases = load_incident_cases()
    elif set_name == "holdout":
        all_cases = load_holdout_cases()
    elif set_name == "all":
        all_cases = list(all_cases) + load_holdout_cases()
    if case_id is not None:
        selected = [case for case in all_cases if case.case_id == case_id]
        if not selected:
            raise CaseError(f"no case with id '{case_id}'")
        return selected
    if set_name in ("full", "holdout", "all", "incidents"):
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
    if any(case.tier in ("holdout", "incident") for case in all_cases):
        raise CaseError("a holdout-tier case is in evals/cases; holdout cases live in evals/holdout")
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

    judged_as = None if arguments.case is not None else arguments.set
    violations = threshold_violations(results, thresholds, judged_as)
    print()
    print(describe_what_was_checked(thresholds, judged_as))
    if violations:
        print("BELOW THRESHOLD")
        for violation in violations:
            print(f"  {violation}")
        return EXIT_BELOW_THRESHOLD
    return 0


def record_command(arguments):
    from eval_runner.model_comparison import record_cases

    all_cases = load_cases(EVALS_DIRECTORY / "cases")
    cases = select_cases(all_cases, arguments.set, arguments.case)
    check_lab_accounts()
    print(f"recording {len(cases)} case(s); each also scores the rule engine end to end",
          flush=True)

    def show_progress(case_result):
        print(f"  {case_result.case.case_id}: expected {case_result.case.expected_root_cause}, "
              f"answered {case_result.answered_root_cause} -> {case_result.outcome}", flush=True)

    aborted_reason = None
    try:
        results = record_cases(cases, on_result=show_progress)
    except RunAborted as aborted:
        results, aborted_reason = aborted.results, str(aborted)
    report_path = save_report(results, "rule_engine (recording run)", aborted_reason=aborted_reason)
    print()
    print(format_report(results, "rule_engine (recording run)", aborted_reason))
    print(f"report saved to {report_path}")
    return EXIT_ABORTED if aborted_reason else 0


def replay_command(arguments):
    from eval_runner.model_comparison import (
        APPROVED_MODELS,
        REPORTS_SUBDIRECTORY,
        SpendLedger,
        calibration_summary,
        format_replay_report,
        load_api_client,
        replay,
        replay_results_as_json,
    )

    models = tuple(arguments.models.split(",")) if arguments.models else APPROVED_MODELS
    unapproved = [model for model in models if model not in APPROVED_MODELS]
    if unapproved:
        raise CaseError(f"models not approved by the owner: {unapproved}")
    all_cases = load_cases(EVALS_DIRECTORY / "cases")
    cases = select_cases(all_cases, arguments.set, arguments.case)
    try:
        client = load_api_client()
    except KeyError:
        print("error: ANTHROPIC_API_KEY is not set in .env; the rule engine needs no key, "
              "model replay does", file=sys.stderr)
        return EXIT_USAGE
    ledger = SpendLedger()
    print(f"replaying {len(cases)} case(s) to {', '.join(models)}, {arguments.repeats} repeat(s); "
          f"spent so far {ledger.total_usd:.2f} of {ledger.cap_usd:.2f} USD", flush=True)

    def show_progress(replay_result):
        r = replay_result.case_result
        print(f"  {replay_result.backend} #{replay_result.repeat} {r.case.case_id}: "
              f"{r.answered_root_cause} -> {r.outcome} ({replay_result.cost_usd:.4f} USD)",
              flush=True)

    results, stop_reason = replay(cases, client, models=models, repeats=arguments.repeats,
                                  ledger=ledger, max_calls=arguments.max_calls,
                                  on_result=show_progress)
    REPORTS_SUBDIRECTORY.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORTS_SUBDIRECTORY / f"{stamp}-replay.json"
    report = replay_results_as_json(results, stop_reason)
    report["text"] = format_replay_report(results, stop_reason)
    report_path.write_text(json.dumps(report, indent=1))
    print()
    print(report["text"])
    print(f"total spent on model calls so far: {ledger.total_usd:.2f} of {ledger.cap_usd:.2f} USD")
    print("cost calibration:")
    print(calibration_summary(ledger, models, len(cases), arguments.repeats))
    print(f"report saved to {report_path}")
    return 0


def retrieval_command(arguments):
    from diagnosis.retrieval import KeywordRetriever
    from eval_runner.retrieval_eval import (
        evaluate_retriever,
        format_retrieval_report,
        load_retrieval_queries,
    )

    queries = load_retrieval_queries(EVALS_DIRECTORY / "retrieval" / "queries.json")
    retriever = KeywordRetriever()
    print(format_retrieval_report(retriever.name, evaluate_retriever(retriever, queries)))
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
    run.add_argument("--set", default="quick",
                     help="'quick' (default), 'full', 'holdout', 'all' (full plus holdout; "
                          "not judged against thresholds, use 'full' for that), "
                          "or a set in evals/sets.json")
    run.add_argument("--case", default=None,
                     help="run a single case by id (with --set holdout for a holdout case)")
    run.add_argument("--backend", default="rule_engine", choices=sorted(BACKENDS))
    commands.add_parser("report", help="print the last report again")
    record = commands.add_parser(
        "record", help="run cases end to end, saving the evidence for replay")
    record.add_argument("--set", default="all")
    record.add_argument("--case", default=None)
    replay_parser = commands.add_parser(
        "replay", help="give recorded evidence to the approved models (costs money)")
    replay_parser.add_argument("--set", default="all")
    replay_parser.add_argument("--case", default=None)
    replay_parser.add_argument("--models", default=None,
                               help="comma-separated; default: all approved models")
    replay_parser.add_argument("--repeats", type=int, default=5)
    replay_parser.add_argument("--max-calls", type=int, default=None,
                               help="stop after this many model calls (for cost calibration)")
    commands.add_parser("retrieval", help="runbook retrieval hit rates (no lab needed)")
    arguments = parser.parse_args(argument_list)
    try:
        return {"run": run_command, "report": report_command, "record": record_command,
                "replay": replay_command, "retrieval": retrieval_command}[arguments.command](arguments)
    except (CaseError, ThresholdError, PreflightError) as problem:
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_USAGE
