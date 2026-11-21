"""The orchestrator: takes an alert, runs the agents, writes one report.

Each round collects a fresh evidence snapshot and gives it to the SQL agent
and the metrics agent. The verdict for a round is the frozen rule engine's
decide() on the same snapshot, which is by construction the combination of
the two agents' assessments (diagnosis.rules.combine). One source of truth
means the orchestrator can never disagree with the rule-engine baseline it is
scored against.

Two exceptions to "the round's verdict is decide()", both because a project
rule outranks agreement with the baseline:

- A round is never accepted as healthy while the metrics agent reports
  metrics missing or the exporter unreachable. A metric that has disappeared
  is evidence of an anomaly, never of health (CLAUDE.md). The frozen rule
  engine misses one such case (Prometheus lacking max_connections); the
  orchestrator turns that round into "undetermined" instead.
- A round whose evidence collection fails is an undetermined round, with the
  failure recorded, never a crash and never healthy.

A round that names a cause, or finds the system healthy, settles the
diagnosis. A round that answers "undetermined" is looked at again after a
short wait: a fault that has just started may be clear a few seconds later.
After three rounds without a settled answer the report says "undetermined,
escalate to a human" and lists what was checked. It never forces a guess.
"""

import dataclasses
import time
from dataclasses import dataclass

from diagnosis.agents.metrics_agent import investigate_metrics
from diagnosis.agents.sql_agent import investigate_sql
from diagnosis.contract import HEALTHY, UNDETERMINED, Diagnosis
from diagnosis.evidence import collect_evidence
from diagnosis.rules import decide

MAX_ROUNDS = 3
SECONDS_BETWEEN_ROUNDS = 5
# Metrics anomalies that forbid a "healthy" verdict.
ANOMALIES_THAT_FORBID_HEALTHY = ("metrics_missing", "exporter_unreachable")


@dataclass(frozen=True)
class DiagnosisReport:
    alert: str
    diagnosis: object  # diagnosis.contract.Diagnosis
    findings: tuple  # AgentFinding from the final round, one per agent
    rounds: tuple  # the Diagnosis of every round, in order


def orchestrate(alert_text, access, collect=collect_evidence, sleep=time.sleep,
                max_rounds=MAX_ROUNDS, seconds_between_rounds=SECONDS_BETWEEN_ROUNDS):
    """Diagnoses the alert in at most max_rounds rounds. Returns a DiagnosisReport."""
    round_verdicts = []
    findings = ()
    for round_number in range(1, max_rounds + 1):
        try:
            evidence = collect(access)
        except Exception as collection_error:
            verdict = Diagnosis(
                root_cause=UNDETERMINED, rounds_used=round_number,
                evidence=(f"evidence collection failed: {type(collection_error).__name__}",),
                rationale=(
                    "Evidence could not be collected in this round "
                    f"({type(collection_error).__name__}), so nothing can be concluded from it."
                ),
            )
        else:
            findings = (investigate_sql(evidence), investigate_metrics(evidence))
            verdict = dataclasses.replace(decide(evidence), rounds_used=round_number)
            verdict = _refuse_healthy_with_missing_metrics(verdict, findings)
        round_verdicts.append(verdict)
        if verdict.root_cause != UNDETERMINED:
            break
        if round_number < max_rounds:
            sleep(seconds_between_rounds)

    final = round_verdicts[-1]
    if final.root_cause == UNDETERMINED and len(round_verdicts) == max_rounds:
        final = dataclasses.replace(
            final,
            rationale=(
                f"Not settled after {max_rounds} rounds of investigation. "
                + final.rationale.rstrip()
                + " Undetermined: escalate to a human."
            ),
        )
    return DiagnosisReport(alert=alert_text, diagnosis=final, findings=findings,
                           rounds=tuple(round_verdicts))


def _refuse_healthy_with_missing_metrics(verdict, findings):
    if verdict.root_cause != HEALTHY:
        return verdict
    metrics_finding = next(f for f in findings if f.agent == "metrics")
    blocking = [anomaly for anomaly in metrics_finding.details.get("anomaly_types", [])
                if anomaly in ANOMALIES_THAT_FORBID_HEALTHY]
    if not blocking:
        return verdict
    missing = metrics_finding.details.get("missing_metrics") or []
    return dataclasses.replace(
        verdict,
        root_cause=UNDETERMINED,
        evidence=verdict.evidence + tuple(f"metrics anomaly: {anomaly}" for anomaly in blocking),
        rationale=(
            "Inside the database nothing explains the alert, but metrics are not complete"
            + (f" ({', '.join(missing)} missing)" if missing else "")
            + ". A metric that has disappeared is a sign of trouble, so this is not reported "
            "as healthy."
        ),
    )


def diagnose_with_orchestrator(alert_text, access):
    """Eval-runner entry point: same signature as every backend."""
    return orchestrate(alert_text, access).diagnosis


# ---- the report ----------------------------------------------------------

CAPTURED_SQL_NOTE = "[captured SQL, untrusted]"


def _mark_captured(fact):
    """Facts that quote a statement are labelled, so a reader (or a model
    later) can tell the system's own words from text a client wrote."""
    fact = _one_line(fact)
    return f"{CAPTURED_SQL_NOTE} {fact}" if "statement reads" in fact else fact


def _one_line(text):
    """Captured text may contain line breaks; a label applies per line, so
    every entry is printed on one."""
    return " ".join(str(text).split())


def render_report_text(report):
    diagnosis = report.diagnosis
    lines = [
        "DIAGNOSIS REPORT",
        f"Alert: {report.alert}",
        "",
        f"ROOT CAUSE: {diagnosis.root_cause}",
        f"Symptoms: {', '.join(diagnosis.symptoms) or 'none'}",
        f"Onset: {diagnosis.onset or 'unknown'}",
        f"Rounds: {diagnosis.rounds_used} of at most {MAX_ROUNDS}"
        + (" (" + " -> ".join(r.root_cause for r in report.rounds) + ")"
           if len(report.rounds) > 1 else ""),
        "",
        "Why (table and column names here can come from captured SQL):",
        f"  {_one_line(diagnosis.rationale)}",
    ]
    if diagnosis.index_suggestion:
        suggestion = diagnosis.index_suggestion
        lines += ["", f"Candidate index (not verified; for the change safety gate; names "
                      f"parsed from captured SQL): "
                      f"{suggestion.table}({', '.join(suggestion.columns)})"]
    lines += ["", "Evidence:"] + [f"  - {_mark_captured(fact)}" for fact in diagnosis.evidence]
    for finding in report.findings:
        title = {"sql": "SQL agent", "metrics": "Metrics agent"}.get(finding.agent, finding.agent)
        lines += ["", f"{title}: {finding.candidate or 'names no cause'}"]
        if finding.weak_signals:
            lines += [f"  weak signal: {signal}" for signal in finding.weak_signals]
        if finding.agent == "metrics" and finding.details.get("anomaly_types"):
            lines.append(f"  anomalies: {', '.join(finding.details['anomaly_types'])}")
        if finding.agent == "sql":
            for statement in finding.details.get("top_statements", []):
                lines.append(
                    f"  {CAPTURED_SQL_NOTE} {_one_line(statement['digest_text'])} "
                    f"({statement['rows_examined_per_execution']:.0f} rows per execution, "
                    f"explain: {statement['explain_status'] or 'not run'})"
                )
    lines += ["", "Recommended steps: (runbook retrieval arrives in step C5)"]
    return "\n".join(lines)


def report_as_dict(report):
    diagnosis = report.diagnosis
    suggestion = diagnosis.index_suggestion
    return {
        "untrusted_fields_note": (
            "evidence, rationale, index_suggestion and findings may contain text or names "
            "taken from captured SQL. Fence them before giving them to a model."
        ),
        "alert": report.alert,
        "root_cause": diagnosis.root_cause,
        "symptoms": list(diagnosis.symptoms),
        "rationale": diagnosis.rationale,
        "onset": diagnosis.onset,
        "evidence": list(diagnosis.evidence),
        "index_suggestion": (
            {"table": suggestion.table, "columns": list(suggestion.columns)} if suggestion else None
        ),
        "rounds_used": diagnosis.rounds_used,
        "rounds": [{"root_cause": r.root_cause, "rationale": r.rationale} for r in report.rounds],
        "findings": [
            {"agent": f.agent, "candidate": f.candidate, "symptoms": list(f.symptoms),
             "rationale": f.rationale, "weak_signals": list(f.weak_signals),
             "details": f.details}
            for f in report.findings
        ],
    }
