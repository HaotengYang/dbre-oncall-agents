"""Recording evidence once, and replaying it to each model (the replay layer).

Record: each case runs end to end on the live lab. The evidence the
diagnosis side collected is saved, and the rule engine's answer on exactly
that evidence is scored (so recording is also an end-to-end rule-engine run).

Replay: every recording is given to every approved model, several times. All
models see identical input. No lab is needed. Spending is counted from each
response's token usage and stops before a call that would cross the cap.

Replay numbers measure model reasoning only. They are always reported under
their own heading and never mixed with end-to-end numbers.
"""

import datetime
import json
import pathlib
import statistics
import time
from dataclasses import dataclass

from diagnosis.access import load_diagnosis_access
from diagnosis.evidence import collect_evidence
from diagnosis.llm import (
    MAX_OUTPUT_TOKENS,
    ModelAnswerError,
    ModelCall,
    PRICES_PER_MILLION,
    ask_model,
    cost_of,
)
from diagnosis.rules import decide
from diagnosis.serialization import evidence_from_dict, evidence_to_dict
from eval_runner.runner import RunAborted, run_case
from eval_runner.scoring import CaseResult, classify_outcome, summarize

STATE_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "var"
RECORDINGS_DIRECTORY = STATE_DIRECTORY / "recordings"
SPEND_LEDGER_PATH = STATE_DIRECTORY / "model_spend.json"
REPORTS_SUBDIRECTORY = STATE_DIRECTORY / "reports"

# Approved by the owner on 2026-11-21 (option A of docs/proposals/C4-models-and-budget.md).
APPROVED_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
SPENDING_CAP_USD = 40.00
# Used until a model's own calls give a measured average.
ESTIMATED_INPUT_TOKENS = 4_000
ESTIMATED_OUTPUT_TOKENS = 2_000
DEFAULT_REPEATS = 5


class BudgetExhausted(Exception):
    """The next call would cross the spending cap."""


class ReplayHalted(Exception):
    """An error that every later call would repeat (bad key, unknown model,
    rejected request). The run stops instead of scoring a model at zero."""


# ---- recording -----------------------------------------------------------


def record_cases(cases, recordings_directory=RECORDINGS_DIRECTORY, on_result=None):
    """Runs each case end to end, saving the evidence and scoring the rule
    engine's answer on it. Returns the end-to-end CaseResults."""
    recordings_directory.mkdir(parents=True, exist_ok=True)
    results = []
    for case in cases:
        captured = {}

        def collect_and_decide(alert_text, access, captured=captured):
            evidence = collect_evidence(access)
            captured["evidence"] = evidence
            return decide(evidence)

        recording_path = recordings_directory / f"{case.case_id}.json"
        try:
            case_result = run_case(case, collect_and_decide)
        except RunAborted as aborted:
            raise RunAborted(str(aborted), results) from aborted
        except Exception as unexpected:
            raise RunAborted(f"case {case.case_id}: {type(unexpected).__name__}: {unexpected}",
                             results) from unexpected
        # Only evidence from a valid case is kept for replay. A case the
        # end-to-end run threw out (the injector's record disagreed with the
        # case) must not be scored in replay either, and an older recording of
        # it must not linger.
        recording_path.unlink(missing_ok=True)
        if "evidence" in captured and case_result.outcome != "invalid":
            recording_path.write_text(json.dumps({
                "case_id": case.case_id,
                "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                    timespec="seconds"),
                "evidence": evidence_to_dict(captured["evidence"]),
            }, indent=1))
        results.append(case_result)
        if on_result is not None:
            on_result(case_result)
    return results


def load_recording(case_id, recordings_directory=RECORDINGS_DIRECTORY):
    path = recordings_directory / f"{case_id}.json"
    if not path.exists():
        return None
    return evidence_from_dict(json.loads(path.read_text())["evidence"])


# ---- spending ------------------------------------------------------------


class SpendLedger:
    """Running total of what model calls have cost, kept on disk across runs."""

    def __init__(self, path=SPEND_LEDGER_PATH, cap_usd=SPENDING_CAP_USD):
        self.path = path
        self.cap_usd = cap_usd
        self.calls = json.loads(path.read_text())["calls"] if path.exists() else []

    @property
    def total_usd(self):
        return sum(call["cost_usd"] for call in self.calls)

    def _measured(self, model):
        return [call for call in self.calls if call["model"] == model and not call.get("estimated")]

    def estimated_next_cost(self, model):
        """Average cost of this model's measured calls (or the assumption)."""
        own_calls = self._measured(model)
        if own_calls:
            input_tokens = statistics.mean(call["input_tokens"] for call in own_calls)
            output_tokens = statistics.mean(call["output_tokens"] for call in own_calls)
        else:
            input_tokens, output_tokens = ESTIMATED_INPUT_TOKENS, ESTIMATED_OUTPUT_TOKENS
        return cost_of(model, input_tokens, output_tokens)

    def worst_case_cost(self, model):
        """The most one call can cost: the largest prompt seen (or assumed)
        plus a full MAX_OUTPUT_TOKENS answer."""
        own_calls = self._measured(model)
        input_tokens = max([call["input_tokens"] for call in own_calls] or [ESTIMATED_INPUT_TOKENS])
        return cost_of(model, input_tokens * 1.5, MAX_OUTPUT_TOKENS)

    def check_room_for(self, model):
        """Refuses a call unless even its worst case fits under the cap."""
        worst_case = self.worst_case_cost(model)
        if self.total_usd + worst_case > self.cap_usd:
            raise BudgetExhausted(
                f"spent {self.total_usd:.2f} USD of {self.cap_usd:.2f}; the next {model} call "
                f"could cost up to {worst_case:.3f} USD and would risk crossing the cap"
            )

    def add_unknown_cost(self, model, case_id):
        """A call that failed without reporting usage is charged at its worst
        case, so the ledger can only overstate what was spent."""
        worst_case = self.worst_case_cost(model)
        self.calls.append({"model": model, "case_id": case_id, "input_tokens": 0,
                           "output_tokens": 0, "cost_usd": worst_case, "seconds": 0,
                           "estimated": True})
        self._save()
        return worst_case

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"cap_usd": self.cap_usd, "calls": self.calls}, indent=1))

    def add(self, model_call, case_id):
        self.calls.append({
            "model": model_call.model, "case_id": case_id,
            "input_tokens": model_call.input_tokens, "output_tokens": model_call.output_tokens,
            "cost_usd": model_call.cost_usd, "seconds": round(model_call.seconds, 2),
        })
        self._save()

    def projected_cost(self, calls_per_model):
        """Expected cost of a run, from measured averages where they exist."""
        return sum(self.estimated_next_cost(model) * count
                   for model, count in calls_per_model.items())


# ---- replay --------------------------------------------------------------


@dataclass(frozen=True)
class ReplayResult:
    backend: str
    repeat: int
    case_result: CaseResult
    cost_usd: float


def _scored(case, diagnosis, seconds):
    suggestion = diagnosis.index_suggestion
    return CaseResult(
        case=case, answered_root_cause=diagnosis.root_cause,
        answered_symptoms=tuple(diagnosis.symptoms),
        outcome=classify_outcome(case, diagnosis.root_cause),
        index_suggestion=(suggestion.table, tuple(suggestion.columns)) if suggestion else None,
        onset_error_seconds=None, seconds_taken=seconds, invalid_reason=None,
    )


def _no_answer(case, reason, seconds):
    return CaseResult(case=case, answered_root_cause="(no answer)", answered_symptoms=(),
                      outcome="diagnosis_error", index_suggestion=None, onset_error_seconds=None,
                      seconds_taken=seconds, invalid_reason=reason)


# Errors every later call would repeat. Anything else (a timeout, a rate
# limit) affects one call and is recorded as "no answer" for that call.
PERSISTENT_ERROR_NAMES = ("AuthenticationError", "PermissionDeniedError", "NotFoundError",
                          "BadRequestError")


def replay(cases, client, models=APPROVED_MODELS, repeats=DEFAULT_REPEATS, ledger=None,
           max_calls=None, recordings_directory=RECORDINGS_DIRECTORY, on_result=None):
    """Replays each case's recorded evidence to the rule engine (once; it is
    deterministic) and to each model (repeats times).

    Calls go case by case and, within a case, model by model, so a small
    max_calls (cost calibration) reaches every model.

    Returns (results, stop_reason). stop_reason is None if everything ran, or
    says why it stopped early (call limit, budget, persistent error). Results
    so far are kept.
    """
    ledger = ledger or SpendLedger()
    recorded = [(case, load_recording(case.case_id, recordings_directory)) for case in cases]
    recorded = [(case, evidence) for case, evidence in recorded if evidence is not None]
    results = []
    for case, evidence in recorded:
        started_at = time.monotonic()
        rule_answer = decide(evidence)
        results.append(ReplayResult("rule_engine", 1,
                                    _scored(case, rule_answer, time.monotonic() - started_at), 0.0))
    calls_made = 0
    for repeat in range(1, repeats + 1):
        for case, evidence in recorded:
            for model in models:
                if max_calls is not None and calls_made >= max_calls:
                    return results, f"stopped after the requested {max_calls} model calls"
                try:
                    ledger.check_room_for(model)
                except BudgetExhausted as exhausted:
                    return results, str(exhausted)
                started_at = time.monotonic()
                cost = 0.0
                try:
                    diagnosis, model_call = ask_model(client, model, case.alert, evidence)
                    case_result = _scored(case, diagnosis, model_call.seconds)
                except ModelAnswerError as failed:
                    model_call = failed.call
                    case_result = _no_answer(case, str(failed), time.monotonic() - started_at)
                except Exception as failed:  # network, timeout, rate limit, API error
                    if type(failed).__name__ in PERSISTENT_ERROR_NAMES:
                        return results, (f"{model}: {type(failed).__name__}; every later call "
                                         "would fail the same way, so the run stopped")
                    model_call = None
                    cost = ledger.add_unknown_cost(model, case.case_id)
                    case_result = _no_answer(
                        case, f"{type(failed).__name__} (charged at its worst case "
                              f"{cost:.3f} USD, since its real cost is unknown)",
                        time.monotonic() - started_at)
                calls_made += 1
                if model_call is not None:
                    ledger.add(model_call, case.case_id)
                    cost = model_call.cost_usd
                replay_result = ReplayResult(model, repeat, case_result, cost)
                results.append(replay_result)
                if on_result is not None:
                    on_result(replay_result)
    return results, None


def calibration_summary(ledger, models, case_count, repeats):
    """What the measured calls say the full comparison will cost."""
    lines = []
    calls_per_model = {model: case_count * repeats for model in models}
    for model in models:
        measured = ledger._measured(model)
        if measured:
            lines.append(
                f"  {model}: {len(measured)} measured call(s), mean "
                f"{statistics.mean(c['input_tokens'] for c in measured):.0f} input / "
                f"{statistics.mean(c['output_tokens'] for c in measured):.0f} output tokens, "
                f"{ledger.estimated_next_cost(model):.4f} USD per call"
            )
        else:
            lines.append(f"  {model}: no measured call yet (assumed "
                         f"{ledger.estimated_next_cost(model):.4f} USD per call)")
    projected = ledger.projected_cost(calls_per_model)
    lines.append(
        f"  projected cost of the full comparison ({case_count} cases x {repeats} repeats x "
        f"{len(models)} models = {sum(calls_per_model.values())} calls): {projected:.2f} USD; "
        f"already spent {ledger.total_usd:.2f} of {ledger.cap_usd:.2f} USD"
    )
    return "\n".join(lines)


def stability(results_for_backend):
    """Share of cases whose repeated answers were all the same."""
    answers_by_case = {}
    for result in results_for_backend:
        answers_by_case.setdefault(result.case_result.case.case_id, set()).add(
            result.case_result.answered_root_cause)
    repeated = {case_id: answers for case_id, answers in answers_by_case.items()
                if sum(1 for r in results_for_backend
                       if r.case_result.case.case_id == case_id) > 1}
    if not repeated:
        return None, 0
    consistent = sum(1 for answers in repeated.values() if len(answers) == 1)
    return consistent, len(repeated)


REPLAY_HEADING = ("REPLAY (recorded evidence, identical for every backend; measures reasoning "
                  "only, not evidence collection)")


def format_replay_report(results, stop_reason=None):
    backends = list(dict.fromkeys(result.backend for result in results))
    lines = [REPLAY_HEADING, ""]
    for tier, heading in (("basic", "BASIC TIER"), ("hard", "HARD TIER"),
                          ("holdout", "HOLDOUT (no thresholds; nothing tuned to it)")):
        rows = [("backend", "hit rate", "false positives", "symptom as cause", "undetermined",
                 "index correct", "stable", "mean s", "cost USD")]
        for backend in backends:
            backend_results = [r for r in results
                               if r.backend == backend and r.case_result.case.tier == tier]
            if not backend_results:
                continue
            summary = summarize([r.case_result for r in backend_results])
            consistent, repeated = stability(backend_results)
            rows.append((
                backend, summary.hit_rate.text(), summary.false_positive_rate.text(),
                summary.symptom_as_cause_rate.text(), summary.undetermined_rate.text(),
                summary.index_suggestion_rate.text(),
                "n/a" if consistent is None else f"{consistent}/{repeated}",
                f"{statistics.mean(r.case_result.seconds_taken for r in backend_results):.1f}",
                f"{sum(r.cost_usd for r in backend_results):.2f}",
            ))
        if len(rows) > 1:
            widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
            lines.append(heading)
            for position, row in enumerate(rows):
                lines.append("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))
                if position == 0:
                    lines.append("  ".join("-" * width for width in widths))
            lines.append("")
    no_answers = [r for r in results if r.case_result.outcome == "diagnosis_error"]
    if no_answers:
        lines.append("NO ANSWER")
        lines += [f"  {r.backend} {r.case_result.case.case_id} (repeat {r.repeat}): "
                  f"{r.case_result.invalid_reason}" for r in no_answers]
        lines.append("")
    total_cost = sum(r.cost_usd for r in results)
    lines.append(f"Model calls: {sum(1 for r in results if r.backend != 'rule_engine')}, "
                 f"cost of this run: {total_cost:.2f} USD")
    if stop_reason:
        lines.append(f"STOPPED EARLY: {stop_reason}")
    return "\n".join(lines)


def replay_results_as_json(results, stop_reason=None):
    return {
        "layer": "replay",
        "stop_reason": stop_reason,
        "results": [
            {"backend": r.backend, "repeat": r.repeat, "case_id": r.case_result.case.case_id,
             "tier": r.case_result.case.tier, "expected": r.case_result.case.expected_root_cause,
             "answered": r.case_result.answered_root_cause,
             "symptoms": list(r.case_result.answered_symptoms), "outcome": r.case_result.outcome,
             "index_suggestion": (list(r.case_result.index_suggestion)
                                  if r.case_result.index_suggestion else None),
             "seconds": round(r.case_result.seconds_taken, 2), "cost_usd": r.cost_usd,
             "note": r.case_result.invalid_reason}
            for r in results
        ],
    }


def load_api_client(env_path=None):
    """An Anthropic client with the key from .env (kept out of the process
    environment and never printed). Raises KeyError if there is no key."""
    import anthropic

    from eval_runner.cli import EVALS_DIRECTORY

    env_path = env_path or EVALS_DIRECTORY.parent / ".env"
    values = {}
    if not env_path.exists():
        raise KeyError("ANTHROPIC_API_KEY")
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name, _, value = stripped.partition("=")
            values[name.strip()] = value.strip().strip("'\"")
    api_key = values.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise KeyError("ANTHROPIC_API_KEY")
    # No automatic retries: a retried timeout can be billed twice while the
    # ledger counts one call. A failed call is charged at its worst case.
    return anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=180.0)


__all__ = ["APPROVED_MODELS", "PRICES_PER_MILLION", "SPENDING_CAP_USD"]
