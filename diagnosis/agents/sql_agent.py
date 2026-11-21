"""The SQL agent: what the database's own state says about the alert.

It looks at sessions, locks and the heaviest statements of one Evidence
snapshot. The judgement is the frozen rule engine's assess_sql, used as it
stands: candidate, symptoms, rationale, facts, index_suggestion and
weak_signals are copied from it unchanged, so this agent's verdict always
equals the rule engine's SQL verdict. No threshold is repeated here.

Untrusted content: facts, rationale and index_suggestion can contain table
and column names taken from captured SQL (the index suggestion is parsed
from a client's statement). They are fine to show a human, and must go
through diagnosis.untrusted before reaching a model prompt.

Without a database connection (evidence.sessions is None) nothing inside can
be judged. The finding then names no candidate, states no facts from inside,
and carries one weak signal saying the database could not be inspected.

details keys:

- "blocking_transactions": list of {"rows_locked": int, "holder_is_idle":
  bool}, one per transaction other sessions waited on, as in the evidence.
- "top_statements": list of up to 3 of the heaviest statements, heaviest
  first by rows examined per second, each {"digest_text": str cut to 200 characters,
  "rows_examined_per_execution": float, "executions_per_second": float,
  "no_index_used": bool, "explain_status": "explained", "skipped", "failed" or
  None if not explained, "explain_reason": str or None, "full_scan": bool if
  explained, else None}. digest_text and explain_reason quote captured SQL:
  they are UNTRUSTED and must go through diagnosis.untrusted before reaching
  a model prompt.
- "connection_path": "normal", "admin" or "none".
- "sessions": {"total", "max_connections", "idle", "running",
  "row_lock_waiters", "metadata_lock_waiters", "lock_waiters_peak"} as ints,
  or None without a connection.
"""

from diagnosis.agents.base import AgentFinding
from diagnosis.contract import CONNECTION_EXHAUSTION
from diagnosis.explain import EXPLAINED
from diagnosis.rules import assess_sql

AGENT_NAME = "sql"
TOO_MANY_CONNECTIONS_ERROR = 1040
TOP_STATEMENT_COUNT = 3
DIGEST_TEXT_LIMIT = 200


def _statement_detail(load):
    explain = load.explain
    return {
        "digest_text": (load.digest_text or "")[:DIGEST_TEXT_LIMIT],
        "rows_examined_per_execution": load.rows_examined_per_execution,
        "executions_per_second": load.executions_per_second,
        "no_index_used": load.no_index_used,
        "explain_status": explain.status if explain is not None else None,
        "explain_reason": explain.reason if explain is not None else None,
        "full_scan": explain.has_full_scan if explain is not None and explain.status == EXPLAINED
        else None,
    }


def _session_detail(sessions):
    return {
        "total": sessions.total,
        "max_connections": sessions.max_connections,
        "idle": sessions.idle,
        "running": sessions.running,
        "row_lock_waiters": sessions.row_lock_waiters,
        "metadata_lock_waiters": sessions.metadata_lock_waiters,
        "lock_waiters_peak": sessions.lock_waiters_peak,
    }


def _not_inspectable(evidence):
    if evidence.admin_port_tried:
        how = "the normal port and the administrative port both failed"
    else:
        how = "the normal port failed and no administrative port is available"
    if evidence.normal_port_error_code is not None:
        how += f" (normal port error {evidence.normal_port_error_code})"
    signal = f"the database could not be inspected from inside: {how}"
    # A refusal with "too many connections" is an outside observation, not a
    # fact from inside, and it is what the rule engine also reports.
    symptoms = (
        (CONNECTION_EXHAUSTION,)
        if evidence.normal_port_error_code == TOO_MANY_CONNECTIONS_ERROR else ()
    )
    return AgentFinding(
        agent=AGENT_NAME, candidate=None, symptoms=symptoms, facts=(), weak_signals=(signal,),
        rationale=(
            "The database could not be inspected from inside, so sessions, locks and "
            "statements cannot be judged. This agent names no cause."
        ),
        details={"blocking_transactions": [], "top_statements": [],
                 "connection_path": evidence.connection_path, "sessions": None},
    )


def investigate_sql(evidence):
    """Judges one Evidence snapshot from the database's side. Returns an
    AgentFinding whose verdict is exactly assess_sql's."""
    if evidence.sessions is None or evidence.connection_path == "none":
        return _not_inspectable(evidence)
    assessment = assess_sql(evidence)
    sessions = evidence.sessions
    return AgentFinding(
        agent=AGENT_NAME,
        candidate=assessment.candidate,
        symptoms=assessment.symptoms,
        rationale=assessment.rationale,
        facts=assessment.facts,
        weak_signals=assessment.weak_signals,
        index_suggestion=assessment.index_suggestion,
        details={
            "blocking_transactions": [
                {"rows_locked": rows_locked, "holder_is_idle": holder_is_idle}
                for rows_locked, holder_is_idle in sessions.blocking_transactions
            ],
            "top_statements": [
                _statement_detail(load) for load in sorted(
                    evidence.statements, key=lambda load: load.rows_examined_per_second,
                    reverse=True,
                )[:TOP_STATEMENT_COUNT]
            ],
            "connection_path": evidence.connection_path,
            "sessions": _session_detail(sessions),
        },
    )
