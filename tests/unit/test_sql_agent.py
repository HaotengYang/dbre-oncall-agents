"""The SQL agent, on hand-built evidence. Its verdict must always be the frozen
rule engine's SQL verdict; what it adds is the structured details."""

import pytest

from diagnosis.agents.base import AgentFinding
from diagnosis.agents.sql_agent import investigate_sql
from diagnosis.evidence import Evidence, MetricsEvidence, SessionEvidence, StatementLoad
from diagnosis.explain import ExplainResult, PlanRow
from diagnosis.rules import assess_sql

GOOD_METRICS = MetricsEvidence(
    exporter_reaches_mysql=True, connections_now=5.0, connections_last_known=5.0,
    max_connections=60.0, connections_change_per_second=0.0, missing=(),
)
QUIET_SESSIONS = SessionEvidence(
    total=5, max_connections=60, idle=5, running=0, metadata_lock_waiters=0,
    row_lock_waiters=0, blocking_transactions=(), sample_count=6, lock_waiters_peak=0,
)
INDEXED_STATEMENT = StatementLoad(
    digest_text="SELECT ... FROM `orders` WHERE `id` = ?", sample_text="SELECT id FROM orders WHERE id = 7",
    schema_name="shop", sample_truncated=False, explain=None,
    executions=30, executions_per_second=6.0, rows_examined_per_execution=1.0,
    rows_examined_per_second=6.0, no_index_used=False, average_latency_ms=0.5,
)
FULL_SCAN_PLAN = ExplainResult(
    "explained", None, (PlanRow("orders", "ALL", None, 199_000, "Using filesort"),)
)
CUSTOMER_SCAN = StatementLoad(
    digest_text="SELECT ... FROM `orders` WHERE `customer_id` = ? ORDER BY `created_at` DESC LIMIT ?",
    sample_text=("SELECT id, status, total_cents, created_at FROM orders "
                 "WHERE customer_id = 90 ORDER BY created_at DESC LIMIT 10"),
    schema_name="shop", sample_truncated=False, explain=FULL_SCAN_PLAN,
    executions=25, executions_per_second=5.0, rows_examined_per_execution=200_000.0,
    rows_examined_per_second=1_000_000.0, no_index_used=True, average_latency_ms=30.0,
)
SKIPPED_SCAN = StatementLoad(**{
    **CUSTOMER_SCAN.__dict__,
    "digest_text": "SELECT ... FROM `order_items` WHERE `product_id` = ?",
    "explain": ExplainResult("skipped", "contains a comment"),
    "rows_examined_per_second": 500_000.0,
})


def evidence(sessions=QUIET_SESSIONS, statements=(INDEXED_STATEMENT,), metrics=GOOD_METRICS,
             path="normal", normal_error=None, admin_tried=False, admin_error=None):
    return Evidence(
        connection_path=path, normal_port_error_code=normal_error, admin_port_tried=admin_tried,
        admin_port_error_code=admin_error, metrics=metrics, sessions=sessions,
        statements=tuple(statements), onset="2026-11-21T10:00:00+00:00",
    )


def sessions(**changes):
    return SessionEvidence(**{**QUIET_SESSIONS.__dict__, **changes})


SNAPSHOTS = {
    "healthy": evidence(),
    "row_lock_wait": evidence(sessions(total=6, idle=2, row_lock_waiters=3, lock_waiters_peak=4,
                                       blocking_transactions=((50_000, True), (12, False)))),
    "metadata_lock_wait": evidence(sessions(total=8, idle=2, metadata_lock_waiters=5,
                                            lock_waiters_peak=5)),
    "missing_index_explained": evidence(statements=[CUSTOMER_SCAN, INDEXED_STATEMENT]),
    "full_and_idle": evidence(sessions(total=60, idle=57, running=1), path="admin",
                              normal_error=1040, admin_tried=True),
    "full_because_of_locks": evidence(
        sessions(total=60, idle=3, running=1, row_lock_waiters=56, lock_waiters_peak=56,
                 blocking_transactions=((50_000, True),)),
        path="admin", normal_error=1040, admin_tried=True,
    ),
    "weak_signals_only": evidence(sessions(total=20, idle=18, row_lock_waiters=1,
                                           lock_waiters_peak=2)),
}

DOCUMENTED_KEYS = {"blocking_transactions", "top_statements", "connection_path", "sessions"}
STATEMENT_KEYS = {"digest_text", "rows_examined_per_execution", "executions_per_second",
                  "no_index_used", "explain_status", "explain_reason", "full_scan"}
SESSION_KEYS = {"total", "max_connections", "idle", "running", "row_lock_waiters",
                "metadata_lock_waiters", "lock_waiters_peak"}


@pytest.mark.parametrize("snapshot_name", sorted(SNAPSHOTS))
def test_verdict_is_exactly_the_frozen_rule_engines_sql_verdict(snapshot_name):
    snapshot = SNAPSHOTS[snapshot_name]

    finding = investigate_sql(snapshot)
    assessment = assess_sql(snapshot)

    assert isinstance(finding, AgentFinding)
    assert finding.agent == "sql"
    assert finding.candidate == assessment.candidate
    assert finding.symptoms == assessment.symptoms
    assert finding.rationale == assessment.rationale
    assert finding.facts == assessment.facts
    assert finding.index_suggestion == assessment.index_suggestion
    assert finding.weak_signals == assessment.weak_signals


def test_the_snapshots_cover_each_verdict_the_sql_side_can_reach():
    candidates = {name: investigate_sql(snapshot).candidate for name, snapshot in SNAPSHOTS.items()}

    assert candidates["healthy"] is None
    assert candidates["row_lock_wait"] == "lock_wait"
    assert candidates["metadata_lock_wait"] == "lock_wait"
    assert candidates["missing_index_explained"] == "missing_index_slow_query"
    assert candidates["full_and_idle"] == "connection_exhaustion"
    assert candidates["full_because_of_locks"] == "lock_wait"
    assert investigate_sql(SNAPSHOTS["full_because_of_locks"]).symptoms == ("connection_exhaustion",)
    assert investigate_sql(SNAPSHOTS["missing_index_explained"]).index_suggestion.columns == (
        "customer_id", "created_at")
    assert investigate_sql(SNAPSHOTS["weak_signals_only"]).weak_signals != ()


@pytest.mark.parametrize("snapshot_name", sorted(SNAPSHOTS))
def test_details_have_exactly_the_documented_keys(snapshot_name):
    details = investigate_sql(SNAPSHOTS[snapshot_name]).details

    assert set(details) == DOCUMENTED_KEYS
    assert set(details["sessions"]) == SESSION_KEYS
    for statement in details["top_statements"]:
        assert set(statement) == STATEMENT_KEYS


def test_details_report_the_blocking_transactions_and_session_counts():
    details = investigate_sql(SNAPSHOTS["row_lock_wait"]).details

    assert details["blocking_transactions"] == [
        {"rows_locked": 50_000, "holder_is_idle": True},
        {"rows_locked": 12, "holder_is_idle": False},
    ]
    assert details["sessions"] == {
        "total": 6, "max_connections": 60, "idle": 2, "running": 0, "row_lock_waiters": 3,
        "metadata_lock_waiters": 0, "lock_waiters_peak": 4,
    }
    assert details["connection_path"] == "normal"
    assert investigate_sql(SNAPSHOTS["full_and_idle"]).details["connection_path"] == "admin"


def test_top_statements_carry_the_explain_result():
    details = investigate_sql(
        evidence(statements=[CUSTOMER_SCAN, SKIPPED_SCAN, INDEXED_STATEMENT])
    ).details

    explained, skipped, unexplained = details["top_statements"]
    assert explained == {
        "digest_text": CUSTOMER_SCAN.digest_text, "rows_examined_per_execution": 200_000.0,
        "executions_per_second": 5.0, "no_index_used": True, "explain_status": "explained",
        "explain_reason": None, "full_scan": True,
    }
    assert skipped["explain_status"] == "skipped"
    assert skipped["explain_reason"] == "contains a comment"
    assert skipped["full_scan"] is None
    assert unexplained["explain_status"] is None
    assert unexplained["explain_reason"] is None
    assert unexplained["full_scan"] is None
    assert unexplained["no_index_used"] is False


def test_an_explained_plan_without_a_full_scan_says_so():
    index_lookup = StatementLoad(**{**CUSTOMER_SCAN.__dict__, "explain": ExplainResult(
        "explained", None, (PlanRow("orders", "ref", "idx_customer", 20, None),))})

    statement = investigate_sql(evidence(statements=[index_lookup])).details["top_statements"][0]

    assert statement["full_scan"] is False


def test_top_statements_are_at_most_three_heaviest_first_and_cut_at_200_characters():
    long_digest = "SELECT " + "`column_name` , " * 40 + "FROM `orders`"
    assert len(long_digest) > 200
    many = [
        StatementLoad(**{**INDEXED_STATEMENT.__dict__, "digest_text": f"{position} {long_digest}",
                         "rows_examined_per_second": 1000.0 - position})
        for position in range(5)
    ]
    many.reverse()  # lightest first: the agent must order them itself

    top = investigate_sql(evidence(statements=many)).details["top_statements"]

    assert len(top) == 3
    assert [statement["digest_text"][:1] for statement in top] == ["0", "1", "2"]
    assert all(len(statement["digest_text"]) == 200 for statement in top)
    assert top[0]["digest_text"] == f"0 {long_digest}"[:200]


def test_no_statements_gives_an_empty_list():
    assert investigate_sql(evidence(statements=[])).details["top_statements"] == []


@pytest.mark.parametrize("admin_tried", [False, True])
def test_without_a_connection_nothing_from_inside_is_claimed(admin_tried):
    unreachable = evidence(sessions=None, statements=(), path="none", normal_error=1040,
                           admin_tried=admin_tried, admin_error=1040 if admin_tried else None)

    finding = investigate_sql(unreachable)

    assert finding.agent == "sql"
    assert finding.candidate is None
    assert finding.facts == ()
    assert finding.symptoms == ()
    assert finding.index_suggestion is None
    assert finding.details == {"blocking_transactions": [], "top_statements": [],
                               "connection_path": "none", "sessions": None}
    assert any("could not be inspected" in signal for signal in finding.weak_signals)
    assert "could not be inspected" in finding.rationale
