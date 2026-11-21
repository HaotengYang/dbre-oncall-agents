"""Evidence collection against the real lab: the lock-wait evidence is stable
across repeated collections, EXPLAIN is run only where it is safe, and the
collector changes nothing."""

import pymysql
import pytest

from diagnosis.access import load_diagnosis_access
from diagnosis.evidence import collect_evidence
from diagnosis.explain import EXPLAINED, FAILED, SKIPPED, explain_statement
from diagnosis.rules import decide
from tests.lab.conftest import fetch_value
from tests.lab.fault_helpers import (
    clear_and_wait_for_baseline,
    row_counts,
    schema_snapshot,
    start_fault,
    stop_fault,
)

pytestmark = pytest.mark.lab

REPEATS = 8


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def open_diag(lab_env):
    def open_connection(database, read_timeout):
        return pymysql.connect(
            host="127.0.0.1", port=int(lab_env.get("MYSQL_HOST_PORT", "13306")),
            user="shop_diag", password=lab_env["DIAG_DB_PASSWORD"], database=database,
            autocommit=True, connect_timeout=5, read_timeout=read_timeout,
        )
    return open_connection


def test_lock_wait_is_diagnosed_the_same_way_every_time():
    """The fix for the unstable basic case: eight collections in a row during
    the long-transaction fault must all see at least two waiters."""
    access = load_diagnosis_access()
    injector = start_fault("lock_wait.long_transaction")
    try:
        collected = [collect_evidence(access) for _ in range(REPEATS)]
    finally:
        stop_fault(injector)

    waiter_counts = [evidence.sessions.row_lock_waiters for evidence in collected]
    answers = [decide(evidence).root_cause for evidence in collected]
    assert all(count >= 2 for count in waiter_counts), waiter_counts
    assert answers == ["lock_wait"] * REPEATS, list(zip(waiter_counts, answers))
    assert all(evidence.sessions.sample_count == 6 for evidence in collected)
    # The blocker is identified: one idle transaction holding the row locks.
    for evidence in collected:
        assert any(rows_locked >= 50_000 and holder_is_idle
                   for rows_locked, holder_is_idle in evidence.sessions.blocking_transactions)


def test_healthy_lab_shows_no_waiters_in_any_of_several_collections():
    access = load_diagnosis_access()

    collected = [collect_evidence(access) for _ in range(3)]

    assert [evidence.sessions.row_lock_waiters for evidence in collected] == [0, 0, 0]
    assert [decide(evidence).root_cause for evidence in collected] == ["healthy"] * 3


def test_dropped_index_scan_is_explained_and_confirmed_as_a_full_scan():
    access = load_diagnosis_access()
    injector = start_fault("missing_index.dropped_index")
    try:
        evidence = collect_evidence(access)
    finally:
        stop_fault(injector)

    scan = next(load for load in evidence.statements
                if load.no_index_used and "customer_id" in load.digest_text)
    assert scan.explain.status == EXPLAINED
    assert scan.explain.has_full_scan
    assert scan.explain.plan[0].table == "orders" and scan.explain.plan[0].key is None
    diagnosis = decide(evidence)
    assert diagnosis.root_cause == "missing_index_slow_query"
    assert any("EXPLAIN confirms a full table scan" in fact for fact in diagnosis.evidence)


def test_statement_with_a_hostile_comment_is_reported_but_never_explained(lab_env):
    access = load_diagnosis_access()
    injector = start_fault("adversarial.lock_wait_with_hostile_comment")
    try:
        evidence = collect_evidence(access)
    finally:
        stop_fault(injector)

    hostile = [load for load in evidence.statements
               if load.sample_text and "AI ASSISTANT" in load.sample_text]
    assert hostile, "the hostile statement should be among the collected statements"
    for load in hostile:
        assert load.explain is None or load.explain.status == SKIPPED
    refused = explain_statement(open_diag(lab_env), hostile[0].sample_text, "shop")
    assert refused.status == SKIPPED and refused.reason == "contains a comment"
    assert decide(evidence).root_cause == "lock_wait"


def test_refused_statements_cost_the_server_nothing(lab_env):
    """A derived table is executed by plain EXPLAIN (3.0 s measured). The
    safety check must stop it before any connection is opened."""
    import time

    started_at = time.monotonic()
    result = explain_statement(open_diag(lab_env),
                               "SELECT * FROM (SELECT SLEEP(3) AS x) AS derived", "shop")

    assert result.status == SKIPPED
    assert time.monotonic() - started_at < 0.5


def test_a_safe_statement_is_explained_on_the_real_server_and_bad_sql_fails_cleanly(lab_env):
    opener = open_diag(lab_env)

    indexed = explain_statement(opener, "SELECT id FROM orders WHERE customer_id = 77", "shop")
    broken = explain_statement(opener, "SELECT id FROM no_such_table WHERE id = 1", "shop")

    assert indexed.status == EXPLAINED and not indexed.has_full_scan
    assert indexed.plan[0].key == "idx_orders_customer_created"
    assert broken.status == FAILED and "1146" in broken.reason


def test_collecting_evidence_changes_nothing(connect_as):
    diag_connection = connect_as("shop_diag")
    schema_before = schema_snapshot(diag_connection)
    counts_before = row_counts(diag_connection)
    access = load_diagnosis_access()

    for _ in range(2):
        collect_evidence(access)

    assert schema_snapshot(diag_connection) == schema_before
    assert row_counts(diag_connection) == counts_before
    # No session of the diagnosis account is left behind.
    assert fetch_value(
        diag_connection,
        "SELECT COUNT(*) FROM performance_schema.processlist "
        "WHERE USER = 'shop_diag' AND ID <> CONNECTION_ID()",
    ) == 0


def test_no_admin_account_is_locked_out_and_evidence_says_so():
    access = load_diagnosis_access(admin_port_available=False)
    injector = start_fault("connection_exhaustion.idle_flood")
    try:
        evidence = collect_evidence(access)
    finally:
        stop_fault(injector)

    assert evidence.connection_path == "none" and evidence.sessions is None
    assert evidence.normal_port_error_code == 1040 and evidence.admin_port_tried is False
    diagnosis = decide(evidence)
    assert diagnosis.root_cause == "undetermined"
    assert diagnosis.symptoms == ("connection_exhaustion",)
