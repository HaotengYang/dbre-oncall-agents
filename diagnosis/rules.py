"""The rule engine: evidence in, diagnosis out. No model, no network.

It is the no-API-key fallback and the baseline a model has to beat. Every
threshold is named and explained, because a rule engine's whole value is that
its reasons can be read.

The decision is made in three parts so that the agents can reuse them:
assess_sql (sessions, locks, statements), assess_metrics (trends and gaps),
and combine (merging the two). decide() runs all three.

The evidence texts produced here quote captured SQL (statement digests). They
are for a human reading the report. Anything built from them for a model
prompt is untrusted as a whole and must go through diagnosis.untrusted.

FROZEN. Once step C1 is complete this file is tagged in git and its content is
pinned by a test. It must not be changed to pass the holdout cases. Any change
needs the owner's explicit consent (CLAUDE.md).
"""

import re
from dataclasses import dataclass
from typing import Optional

from diagnosis.contract import (
    CONNECTION_EXHAUSTION,
    HEALTHY,
    LOCK_WAIT,
    MISSING_INDEX,
    UNDETERMINED,
    Diagnosis,
    IndexSuggestion,
)

TOO_MANY_CONNECTIONS_ERROR = 1040

# Connections count as used up at this share of the limit. Not 100%: sessions
# come and go while the snapshot is taken.
NEARLY_FULL_SHARE = 0.85
# With connections used up, the cause is whatever most sessions are doing.
MAJORITY_SHARE = 0.5
# The two waiter counts below are sustained levels: the number of waiting
# sessions seen in at least two samples of the observation window, not a
# single instant (see SessionEvidence).
# Sessions waiting on a metadata lock. One or two can be an ordinary schema
# change passing through; three means traffic is queuing behind it.
METADATA_LOCK_WAITERS_FOR_FAULT = 3
# Sessions waiting on row locks. A single sustained waiter is ordinary
# contention.
ROW_LOCK_WAITERS_FOR_FAULT = 2
# A statement is treated as a scan when it uses no index and reads this many
# rows per execution.
SCAN_ROWS_PER_EXECUTION = 10_000
# A scan only matters if it runs often. A report that runs once in a while is
# slow and harmless.
SCAN_MINIMUM_EXECUTIONS = 3
# Rows read per second by scans, above which they are the problem. The lab's
# whole normal workload reads under 200 rows per second.
SCAN_ROWS_PER_SECOND_FOR_FAULT = 250_000
# A connection leak: this many idle sessions and still climbing.
IDLE_SESSIONS_FOR_LEAK = 15
CONNECTIONS_CLIMBING_PER_SECOND = 0.3


def suggest_index(sample_text):
    """A heuristic index for one single-table SELECT: equality columns first,
    then one range or ORDER BY column. Returns None when it cannot tell."""
    if not sample_text or re.search(r"\bJOIN\b", sample_text, re.IGNORECASE):
        return None
    table_match = re.search(r"\bFROM\s+([`\w.]+)", sample_text, re.IGNORECASE)
    where_match = re.search(
        r"\bWHERE\b(.*?)(?:\bORDER\s+BY\b|\bGROUP\s+BY\b|\bLIMIT\b|$)", sample_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match or not where_match:
        return None

    def bare(name):
        return name.replace("`", "").split(".")[-1]

    where_clause = where_match.group(1)
    equality_columns = [bare(c) for c in re.findall(r"([`\w.]+)\s*=(?!=)", where_clause)]
    range_columns = [
        bare(c) for c in re.findall(r"([`\w.]+)\s*(?:>=|<=|<>|>|<|\bBETWEEN\b)", where_clause,
                                    re.IGNORECASE)
    ]
    order_match = re.search(r"\bORDER\s+BY\s+([`\w.]+)", sample_text, re.IGNORECASE)
    order_columns = [bare(order_match.group(1))] if order_match else []

    columns = []
    for column in equality_columns + (range_columns[:1] or order_columns[:1]):
        if column not in columns:
            columns.append(column)
    if not columns:
        return None
    return IndexSuggestion(table=bare(table_match.group(1)), columns=tuple(columns))


def _outside_only(evidence):
    """No database connection at all: conclude from outside signals only."""
    observations = []
    symptoms = ()
    if evidence.normal_port_error_code == TOO_MANY_CONNECTIONS_ERROR:
        observations.append("MySQL refused a new connection with 'too many connections' (1040)")
        symptoms = (CONNECTION_EXHAUSTION,)
    elif evidence.normal_port_error_code is not None:
        observations.append(f"connecting to MySQL failed with error {evidence.normal_port_error_code}")
    if evidence.metrics.exporter_reaches_mysql is not True:
        observations.append("the metrics exporter cannot reach MySQL either")
    if evidence.metrics.missing:
        observations.append("metrics have disappeared: " + ", ".join(evidence.metrics.missing))
    if evidence.metrics.connections_last_known is not None:
        observations.append(
            f"last known connection count was {evidence.metrics.connections_last_known:.0f}"
        )
    admin_note = (
        "The administrative port was tried and also failed."
        if evidence.admin_port_tried else "No administrative port is available."
    )
    return Diagnosis(
        root_cause=UNDETERMINED,
        symptoms=symptoms,
        rationale=(
            "The database cannot be inspected from inside. " + admin_note + " "
            + ("From outside, connections are exhausted. " if symptoms else "")
            + "What is using the connections, and why, cannot be told without getting in: "
            "idle sessions, sessions stuck behind a lock and slow queries piling up all look "
            "the same from here. Disappearing metrics are treated as a sign of trouble, not "
            "of health. Escalate to a human."
        ),
        evidence=tuple(observations),
        onset=evidence.onset,
    )


@dataclass(frozen=True)
class SqlAssessment:
    """What the database's own state says: sessions, locks, statements.

    candidate is a root cause this side can name by itself, or None.
    connections_full_unexplained means connections are used up and no single
    explanation covers most of them. idle_sessions_elevated is only half of a
    leak: the other half, "still climbing", is a metrics observation.
    """

    candidate: Optional[str]
    symptoms: tuple
    rationale: str
    facts: tuple
    index_suggestion: Optional[IndexSuggestion]
    weak_signals: tuple
    connections_full_unexplained: bool
    idle_sessions_elevated: bool


@dataclass(frozen=True)
class MetricsAssessment:
    """What the metrics say, and what their absence says."""

    connections_climbing: bool
    connections_change_per_second: float
    weak_signals: tuple


def _session_fact(evidence):
    sessions = evidence.sessions
    via = " (seen through the administrative port)" if evidence.connection_path == "admin" else ""
    return (
        f"{sessions.total} of {sessions.max_connections} connections in use{via}: "
        f"{sessions.idle} idle, {sessions.running} running, "
        f"{sessions.row_lock_waiters} waiting on row locks, "
        f"{sessions.metadata_lock_waiters} waiting on a metadata lock"
    )


def _frequent_scans(evidence):
    return [
        load for load in evidence.statements
        if load.no_index_used
        and load.rows_examined_per_execution >= SCAN_ROWS_PER_EXECUTION
        and load.executions >= SCAN_MINIMUM_EXECUTIONS
    ]


def _explain_note(load):
    """One clause on what EXPLAIN said about a statement, or an empty string."""
    explain = getattr(load, "explain", None)
    if explain is None:
        return ""
    if explain.status == "explained":
        if explain.has_full_scan:
            return " EXPLAIN confirms a full table scan."
        access_types = [row.access_type for row in explain.plan if row.access_type]
        if not access_types:
            return " EXPLAIN gave no table access to judge."
        if "index" in access_types:
            return " EXPLAIN shows a scan of a whole index."
        if all(row.key for row in explain.plan if row.access_type):
            return " EXPLAIN shows an index lookup now, so the plan may have changed since."
        return " EXPLAIN shows no full table scan (access: " + ", ".join(access_types) + ")."
    return f" It was not explained: {explain.reason}."


def assess_sql(evidence):
    """Judges sessions, locks and statements. Needs a database connection."""
    sessions = evidence.sessions
    session_fact = _session_fact(evidence)
    lock_waiters = sessions.row_lock_waiters + sessions.metadata_lock_waiters
    scans = _frequent_scans(evidence)
    scan_rows_per_second = sum(load.rows_examined_per_second for load in scans)
    worst_scan = scans[0] if scans else None

    def lock_facts():
        facts = [session_fact]
        for rows_locked, holder_is_idle in sessions.blocking_transactions:
            facts.append(
                f"a transaction holding {rows_locked} row locks is blocking others"
                + (" and its session is idle" if holder_is_idle else "")
            )
        return tuple(facts)

    def scan_facts():
        return (session_fact,) + tuple(
            f"statement reads {load.rows_examined_per_execution:.0f} rows per execution with "
            f"no index, {load.executions_per_second:.1f} times a second: {load.digest_text[:120]}"
            + _explain_note(load)
            for load in scans[:2]
        )

    def named(candidate, rationale, facts, symptoms=(), index_suggestion=None):
        return SqlAssessment(
            candidate=candidate, symptoms=symptoms, rationale=rationale, facts=facts,
            index_suggestion=index_suggestion, weak_signals=(),
            connections_full_unexplained=False, idle_sessions_elevated=False,
        )

    def missing_index(symptoms=()):
        suggestion = suggest_index(worst_scan.sample_text)
        return named(
            MISSING_INDEX, symptoms=symptoms, facts=scan_facts(), index_suggestion=suggestion,
            rationale=(
                f"Statements that use no index are reading about {scan_rows_per_second:,.0f} rows "
                "a second, against a few hundred for the normal workload. "
                + ("Copies of them occupy most connections, so the exhausted connections are a "
                   "consequence, not the cause. " if symptoms else "")
                + ("No sessions are waiting on locks, which rules out a lock wait. "
                   if lock_waiters == 0 else "")
                + (f"A candidate index is {suggestion.table}({', '.join(suggestion.columns)}); "
                   "it is a heuristic and has not been verified with EXPLAIN."
                   if suggestion else "No index could be suggested automatically.")
            ),
        )

    def lock_wait(symptoms=()):
        kind = (
            "a metadata lock: a schema change is waiting behind an open transaction and "
            "everything else is queued behind the schema change"
            if sessions.metadata_lock_waiters >= sessions.row_lock_waiters
            else "row locks held by another transaction"
        )
        return named(
            LOCK_WAIT, symptoms=symptoms, facts=lock_facts(),
            rationale=(
                f"{lock_waiters} sessions are waiting on {kind}. "
                + ("They account for most of the connections in use, so the exhausted "
                   "connections are a consequence, not the cause. " if symptoms else "")
                + ("No frequently run statement is scanning without an index, which rules out a "
                   "missing index. " if not scans else "")
            ),
        )

    nearly_full = sessions.total >= NEARLY_FULL_SHARE * sessions.max_connections
    if nearly_full:
        if lock_waiters >= MAJORITY_SHARE * sessions.total:
            return lock_wait(symptoms=(CONNECTION_EXHAUSTION,))
        if scans and sessions.running >= MAJORITY_SHARE * sessions.total:
            return missing_index(symptoms=(CONNECTION_EXHAUSTION,))
        if sessions.idle >= MAJORITY_SHARE * sessions.total:
            return named(
                CONNECTION_EXHAUSTION, facts=(session_fact,),
                rationale=(
                    "Connections are used up and most of them are idle: they are being held, "
                    "not worked. No sessions are waiting on locks and no scan is running in "
                    "them, so nothing else explains the count. Look for a leaking pool or too "
                    "many application instances."
                ),
            )
        return SqlAssessment(
            candidate=None, symptoms=(CONNECTION_EXHAUSTION,), facts=(session_fact,),
            rationale=(
                "Connections are used up, but no single explanation covers most of them: not "
                "lock waits, not scans, not idle sessions. Escalate to a human."
            ),
            index_suggestion=None, weak_signals=(), connections_full_unexplained=True,
            idle_sessions_elevated=False,
        )

    if sessions.metadata_lock_waiters >= METADATA_LOCK_WAITERS_FOR_FAULT:
        return lock_wait()
    if sessions.row_lock_waiters >= ROW_LOCK_WAITERS_FOR_FAULT:
        return lock_wait()
    if scan_rows_per_second >= SCAN_ROWS_PER_SECOND_FOR_FAULT:
        return missing_index()

    idle_sessions_elevated = sessions.idle >= IDLE_SESSIONS_FOR_LEAK
    weak_signals = []
    if lock_waiters > 0:
        weak_signals.append(f"{lock_waiters} session(s) waiting on a lock")
    elif sessions.lock_waiters_peak > 0:
        # Seen in one sample only: not sustained, so no fault is named, but it
        # must not be reported as "no sessions are waiting on locks".
        weak_signals.append(
            f"{sessions.lock_waiters_peak} session(s) were waiting on a lock at one moment "
            "during the observation"
        )
    elif sessions.blocking_transactions:
        # A transaction was seen blocking another session, but the wait had
        # ended before it could be counted.
        weak_signals.append(
            "a transaction was briefly seen blocking another session during the observation"
        )
    if scans:
        weak_signals.append("a frequently run statement uses no index, but its load is small")
    if idle_sessions_elevated:
        weak_signals.append(f"{sessions.idle} idle sessions")
    return SqlAssessment(
        candidate=None, symptoms=(), rationale="", facts=(session_fact,), index_suggestion=None,
        weak_signals=tuple(weak_signals), connections_full_unexplained=False,
        idle_sessions_elevated=idle_sessions_elevated,
    )


def assess_metrics(evidence):
    """Judges the metrics: is the connection count climbing, is anything missing."""
    change_per_second = evidence.metrics.connections_change_per_second or 0
    climbing = change_per_second >= CONNECTIONS_CLIMBING_PER_SECOND
    weak_signals = []
    if climbing:
        weak_signals.append("the connection count is climbing")
    if evidence.metrics.exporter_reaches_mysql is not True or evidence.metrics.missing:
        weak_signals.append("metrics are missing or the exporter cannot reach MySQL")
    if evidence.normal_port_error_code is not None:
        # We only got in through the administrative port. Whatever refused the
        # normal one may have passed by now, but it happened.
        weak_signals.append(
            f"the normal port refused a connection (error {evidence.normal_port_error_code})"
        )
    return MetricsAssessment(
        connections_climbing=climbing, connections_change_per_second=change_per_second,
        weak_signals=tuple(weak_signals),
    )


def combine(sql_assessment, metrics_assessment, evidence):
    """Merges the two assessments into one diagnosis.

    A named cause from the database's own state wins. A leak needs both sides:
    idle sessions piled up (SQL) and the count still climbing (metrics). Any
    leftover weak signal, from either side, means "undetermined", never
    "healthy".
    """
    if sql_assessment.candidate is not None:
        return Diagnosis(
            root_cause=sql_assessment.candidate, symptoms=sql_assessment.symptoms,
            rationale=sql_assessment.rationale, evidence=sql_assessment.facts,
            index_suggestion=sql_assessment.index_suggestion, onset=evidence.onset,
        )
    if sql_assessment.connections_full_unexplained:
        return Diagnosis(
            root_cause=UNDETERMINED, symptoms=sql_assessment.symptoms,
            rationale=sql_assessment.rationale, evidence=sql_assessment.facts,
            onset=evidence.onset,
        )
    if sql_assessment.idle_sessions_elevated and metrics_assessment.connections_climbing:
        return Diagnosis(
            root_cause=CONNECTION_EXHAUSTION, evidence=sql_assessment.facts, onset=evidence.onset,
            rationale=(
                f"{evidence.sessions.idle} sessions are idle and the connection count is still "
                f"climbing at {metrics_assessment.connections_change_per_second:.1f} a second. "
                "Connections are being opened and not returned; at this rate the limit will be "
                "reached. No lock waits and no scans are present."
            ),
        )

    weak_signals = sql_assessment.weak_signals + metrics_assessment.weak_signals
    if weak_signals:
        return Diagnosis(
            root_cause=UNDETERMINED, evidence=sql_assessment.facts + weak_signals,
            onset=evidence.onset,
            rationale=(
                "Something is off, but not enough to name a cause: " + "; ".join(weak_signals)
                + ". This may be a fault that has only just started. Look again shortly, or "
                "escalate to a human."
            ),
        )
    return Diagnosis(
        root_cause=HEALTHY, evidence=sql_assessment.facts,
        rationale=(
            "No sessions are waiting on locks, no frequently run statement is scanning without "
            "an index, connections are well under the limit and not climbing, and metrics are "
            "present. Nothing here explains the alert; the cause is probably outside the "
            "database."
        ),
    )


def decide(evidence):
    """Applies the rules to one evidence snapshot."""
    if evidence.connection_path == "none" or evidence.sessions is None:
        return _outside_only(evidence)
    return combine(assess_sql(evidence), assess_metrics(evidence), evidence)
