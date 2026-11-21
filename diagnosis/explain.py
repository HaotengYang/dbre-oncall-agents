"""Runs a plain EXPLAIN on a captured statement, if and only if it is safe.

Never EXPLAIN ANALYZE. One statement per call, on its own read-only
connection, with a timeout. Anything that fails the safety check is skipped
and says why; nothing is rewritten to make it pass.
"""

import re
from dataclasses import dataclass
from typing import Optional

import pymysql

from diagnosis.explain_safety import explain_refusal

EXPLAIN_TIMEOUT_SECONDS = 5
SCHEMA_NAME_PATTERN = re.compile(r"\A[A-Za-z0-9_]{1,64}\Z")

EXPLAINED = "explained"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass(frozen=True)
class PlanRow:
    table: Optional[str]
    access_type: Optional[str]
    key: Optional[str]
    rows_estimate: Optional[int]
    extra: Optional[str]


@dataclass(frozen=True)
class ExplainResult:
    status: str  # EXPLAINED, SKIPPED or FAILED
    reason: Optional[str]  # why it was skipped or failed
    plan: tuple = ()

    @property
    def has_full_scan(self):
        return any(row.access_type == "ALL" for row in self.plan)


def _plan_rows(cursor):
    column_names = [column[0].lower() for column in cursor.description]
    plan = []
    for raw_row in cursor.fetchall():
        row = dict(zip(column_names, raw_row))
        rows_estimate = row.get("rows")
        plan.append(PlanRow(
            table=row.get("table"), access_type=row.get("type"), key=row.get("key"),
            rows_estimate=int(rows_estimate) if rows_estimate is not None else None,
            extra=row.get("extra"),
        ))
    return tuple(plan)


def explain_statement(open_connection, statement_text, schema_name, truncated=False):
    """Explains one captured statement. Returns an ExplainResult, never raises.

    open_connection(database=..., read_timeout=...) opens a fresh connection as
    the read-only diagnosis account.
    """
    refusal = explain_refusal(statement_text, truncated=truncated)
    if refusal is not None:
        return ExplainResult(SKIPPED, refusal)
    if schema_name is None or not SCHEMA_NAME_PATTERN.match(schema_name):
        return ExplainResult(SKIPPED, "no usable schema name for the statement")

    statement = statement_text.strip().rstrip(";").strip()
    try:
        connection = open_connection(database=schema_name, read_timeout=EXPLAIN_TIMEOUT_SECONDS)
    except pymysql.MySQLError as connect_error:
        return ExplainResult(FAILED, f"could not open a connection ({_code(connect_error)})")

    session_id = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("SELECT CONNECTION_ID()")
            session_id = cursor.fetchone()[0]
            # The only place captured text reaches the server. It has passed
            # the allowlist above and is prefixed with a plain EXPLAIN.
            cursor.execute("EXPLAIN " + statement)
            return ExplainResult(EXPLAINED, None, _plan_rows(cursor))
    except pymysql.MySQLError as explain_error:
        timed_out = _code(explain_error) in (2013, 2014) or "timed out" in str(explain_error).lower()
        if timed_out and session_id is not None:
            return ExplainResult(FAILED, _timeout_reason(open_connection, session_id))
        return ExplainResult(FAILED, f"MySQL refused it (error {_code(explain_error)})")
    except OSError:
        if session_id is not None:
            return ExplainResult(FAILED, _timeout_reason(open_connection, session_id))
        return ExplainResult(FAILED, f"timed out after {EXPLAIN_TIMEOUT_SECONDS} s")
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _code(mysql_error):
    return mysql_error.args[0] if mysql_error.args else None


def _timeout_reason(open_connection, session_id):
    """Ends the stuck session and says honestly whether that worked."""
    if _end_own_session(open_connection, session_id):
        return f"timed out after {EXPLAIN_TIMEOUT_SECONDS} s; its session was ended"
    return (
        f"timed out after {EXPLAIN_TIMEOUT_SECONDS} s and its session {int(session_id)} "
        "could not be ended; it may still be running on the server"
    )


def _end_own_session(open_connection, session_id):
    """Ends this account's own stuck EXPLAIN session. Returns whether it did.

    An account may always end its own sessions; it has no right to end anyone
    else's. Opening the second connection can itself fail, for example when
    the server has no connection left.
    """
    try:
        killer = open_connection(database=None, read_timeout=EXPLAIN_TIMEOUT_SECONDS)
        try:
            with killer.cursor() as cursor:
                cursor.execute(f"KILL {int(session_id)}")
        finally:
            killer.close()
    except Exception:
        return False
    return True
