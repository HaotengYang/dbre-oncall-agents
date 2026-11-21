"""Rehearsal, risk assessment, rollback plan and apply.

Two accounts, never mixed:
- the read-only diagnosis account reads the schema, table sizes and who holds
  metadata locks (it has PROCESS; the gate account does not);
- the gate account creates and drops tables only in shop_shadow, and adds or
  drops indexes in shop (database privileges, tested in the lab).

Every statement the gate sends goes through StatementLog, so tests can check
that nothing but the rendered statements ever reaches shop.
"""

import os
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import pymysql

from change_gate.allowlist import (
    PLAIN_NAME,
    SCHEMA_NAME,
    AcceptedChange,
    RejectedChange,
    TableSchema,
    render_create,
    render_rollback,
    render_shadow_create,
    validate_request,
)
from diagnosis.explain import EXPLAINED, explain_statement
from diagnosis.explain_safety import explain_refusal

SHADOW_SCHEMA = "shop_shadow"
SAMPLE_ROW_LIMIT = 20_000
CHUNK_ROWS = 2_000
PAUSE_BETWEEN_CHUNKS_SECONDS = 0.05
MAX_RUNNING_SESSIONS = 20
LARGE_TABLE_ROWS = 1_000_000
LARGE_TABLE_BYTES = 1024 ** 3
LONG_TRANSACTION_SECONDS = 10
APPLY_LOCK_WAIT_SECONDS = 5
COPY_LOCK_WAIT_SECONDS = 5
STATEMENT_TIMEOUT_SECONDS = 30
SYSTEM_SCHEMAS = ("shop", "shop_shadow", "mysql", "sys", "information_schema",
                  "performance_schema", "lab_meta")

REHEARSAL_LIMITS_NOTE = (
    "The rehearsal ran on a sample of at most {limit} rows. Build time on the sample does not "
    "predict build time on the full table, and the plan on a sample may differ from the plan "
    "on the full table."
)


class GateError(Exception):
    """The gate cannot proceed. The message says why."""


@dataclass
class StatementLog:
    """Every statement the gate sends, with the account that sent it."""

    entries: list = field(default_factory=list)

    def execute(self, connection, account, sql, parameters=None):
        self.entries.append((account, sql))
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall()


@dataclass(frozen=True)
class Rehearsal:
    completed: bool
    sample_rows: int
    online_build_possible: Optional[bool]
    build_seconds: Optional[float]
    query_uses_index: Optional[bool]
    query_note: str
    abandoned_reason: Optional[str]


@dataclass(frozen=True)
class Risk:
    level: str  # "low", "high" or "blocked"
    reasons: tuple
    table_rows: int
    table_bytes: int


@dataclass(frozen=True)
class Plan:
    change: AcceptedChange
    statement: str
    rollback: str
    rehearsal: Rehearsal
    risk: Risk
    limits_note: str


@dataclass(frozen=True)
class GateAccess:
    host: str
    port: int
    diag_user: str
    diag_password: str = field(repr=False)
    gate_user: str = "shop_gate"
    gate_password: str = field(default="", repr=False)


def load_gate_access(environment=None):
    repository_root = pathlib.Path(__file__).resolve().parents[1]
    values = {}
    env_path = repository_root / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                name, _, value = stripped.partition("=")
                values[name.strip()] = value.strip()
    values.update(os.environ if environment is None else environment)
    return GateAccess(
        host="127.0.0.1", port=int(values.get("MYSQL_HOST_PORT", "13306")),
        diag_user="shop_diag", diag_password=values["DIAG_DB_PASSWORD"],
        gate_user="shop_gate", gate_password=values["GATE_DB_PASSWORD"],
    )


def _connect(access, account, database=None, read_timeout=STATEMENT_TIMEOUT_SECONDS):
    """read_timeout=None waits as long as the server takes (used for apply,
    where giving up on the client would not stop the server)."""
    password = access.diag_password if account == access.diag_user else access.gate_password
    return pymysql.connect(host=access.host, port=access.port, user=account, password=password,
                           database=database, autocommit=True, connect_timeout=5,
                           read_timeout=read_timeout)


def read_schema(connection, log, account):
    """The shop tables, their columns and their indexes, from information_schema."""
    columns = log.execute(
        connection, account,
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION", (SCHEMA_NAME,))
    statistics = log.execute(
        connection, account,
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX", (SCHEMA_NAME,))
    tables = {}
    for table_name, column_name in columns:
        tables.setdefault(table_name, {"columns": [], "indexes": {}})["columns"].append(column_name)
    for table_name, index_name, column_name in statistics:
        tables[table_name]["indexes"].setdefault(index_name, []).append(column_name)
    return {name: TableSchema(columns=tuple(t["columns"]),
                              indexes={i: tuple(c) for i, c in t["indexes"].items()})
            for name, t in tables.items()}


def assess_risk(connection, log, account, change, rehearsal):
    rows, data_bytes = log.execute(
        connection, account,
        "SELECT COALESCE(TABLE_ROWS, 0), COALESCE(DATA_LENGTH + INDEX_LENGTH, 0) "
        "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        (SCHEMA_NAME, change.table))[0]
    long_holders = log.execute(
        connection, account,
        "SELECT trx.trx_mysql_thread_id, TIMESTAMPDIFF(SECOND, trx.trx_started, NOW()) "
        "FROM information_schema.INNODB_TRX AS trx "
        "JOIN performance_schema.threads AS thread "
        "  ON thread.PROCESSLIST_ID = trx.trx_mysql_thread_id "
        "JOIN performance_schema.metadata_locks AS mdl "
        "  ON mdl.OWNER_THREAD_ID = thread.THREAD_ID "
        "WHERE mdl.OBJECT_SCHEMA = %s AND mdl.OBJECT_NAME = %s "
        "AND TIMESTAMPDIFF(SECOND, trx.trx_started, NOW()) >= %s",
        (SCHEMA_NAME, change.table, LONG_TRANSACTION_SECONDS))
    return classify_risk(int(rows), int(data_bytes),
                         [int(seconds) for _, seconds in long_holders], rehearsal)


def classify_risk(rows, data_bytes, long_transaction_seconds, rehearsal):
    """The risk decision itself, from measured facts."""
    long_holders = long_transaction_seconds
    reasons = []
    level = "low"
    if int(rows) > LARGE_TABLE_ROWS or int(data_bytes) > LARGE_TABLE_BYTES:
        level = "high"
        reasons.append(f"large table: about {int(rows):,} rows, {int(data_bytes):,} bytes")
    if long_holders:
        if level == "low":
            level = "high"
        oldest = max(long_holders)
        reasons.append(
            f"{len(long_holders)} transaction(s) open for up to {oldest} s hold a metadata lock on "
            "the table; the index build would wait behind them and queue all traffic on the table"
        )
    if rehearsal.online_build_possible is False:
        level = "blocked"
        reasons.append("the index cannot be added online (ALGORITHM=INPLACE, LOCK=NONE refused)")
    if not rehearsal.completed:
        if level == "low":
            level = "high"
        reasons.append(f"the rehearsal did not complete: {rehearsal.abandoned_reason}")
    if rehearsal.online_build_possible is False:
        level = "blocked"  # stays blocked whatever else was found
    if not reasons:
        reasons.append("small table, no long transaction on it, online build rehearsed")
    return Risk(level=level, reasons=tuple(reasons), table_rows=int(rows),
                table_bytes=int(data_bytes))


def _running_sessions(connection, log, account):
    return int(log.execute(
        connection, account,
        "SELECT COUNT(*) FROM performance_schema.processlist "
        "WHERE COMMAND = 'Query' AND USER NOT IN ('event_scheduler', 'system user')")[0][0])


def _primary_key_column(schema, table):
    primary = schema[table].indexes.get("PRIMARY")
    if not primary or len(primary) != 1:
        raise GateError("the rehearsal needs a single-column primary key")
    if not PLAIN_NAME.match(primary[0]):
        raise GateError("the primary key column is not a plain name; the rehearsal will not run")
    return primary[0]


_TABLE_AFTER_KEYWORD = re.compile(r"(?<![A-Za-z0-9_])(?:FROM|JOIN)\s+([A-Za-z0-9_.]+)", re.IGNORECASE)
_QUALIFIER = re.compile(r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*[A-Za-z_]")


def target_query_refusal(target_query, table):
    """Why the target query cannot be explained against the sample, or None.

    Besides the EXPLAIN allowlist: it must read only the table that was copied,
    and must not name a schema, so that it cannot reach the real tables.
    """
    refusal = explain_refusal(target_query)
    if refusal is not None:
        return refusal
    for qualifier in _QUALIFIER.findall(target_query):
        if qualifier.lower() in SYSTEM_SCHEMAS:
            return "names a schema; only the sample copy may be read"
    if len(re.findall(r"\.", target_query)) and re.search(r"\w+\s*\.\s*\w+\s*\.\s*\w+", target_query):
        return "uses a three-part name"
    tables = {name.lower() for name in _TABLE_AFTER_KEYWORD.findall(target_query)}
    if tables != {table}:
        return "reads tables other than the one copied into the sample"
    return None


def _drop_shadow(access, log, shadow_table, gate):
    """Drops the shadow table, on a fresh connection if the old one is dead."""
    try:
        log.execute(gate, access.gate_user, f"DROP TABLE IF EXISTS {shadow_table}")
        return
    except Exception:
        pass
    fresh = _connect(access, access.gate_user)
    try:
        log.execute(fresh, access.gate_user, f"DROP TABLE IF EXISTS {shadow_table}")
    finally:
        fresh.close()


def rehearse(access, log, change, schema, target_query=None):
    """Builds the index on a capped sample copy in shop_shadow, then drops it."""
    shadow_table = f"{SHADOW_SCHEMA}.{change.table}"
    primary_key = _primary_key_column(schema, change.table)
    copied = 0
    online, build_seconds, uses_index, query_note, abandoned = None, None, None, "no target query", None
    diag = _connect(access, access.diag_user)
    try:
        gate = _connect(access, access.gate_user)
    except Exception:
        diag.close()
        raise
    try:
        # The copy reads shop with a consistent read and takes no row locks
        # (READ COMMITTED), and gives up quickly on any lock it does meet, so
        # the rehearsal cannot block application writes.
        log.execute(gate, access.gate_user, "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        log.execute(gate, access.gate_user,
                    f"SET SESSION innodb_lock_wait_timeout = {COPY_LOCK_WAIT_SECONDS}")
        log.execute(gate, access.gate_user, f"SET SESSION lock_wait_timeout = {COPY_LOCK_WAIT_SECONDS}")
        log.execute(gate, access.gate_user, f"DROP TABLE IF EXISTS {shadow_table}")
        log.execute(gate, access.gate_user, f"CREATE TABLE {shadow_table} LIKE {SCHEMA_NAME}.{change.table}")
        last_key = None
        while copied < SAMPLE_ROW_LIMIT:
            if _running_sessions(diag, log, access.diag_user) > MAX_RUNNING_SESSIONS:
                abandoned = "the server is busy; the rehearsal stopped copying to avoid adding load"
                break
            chunk = min(CHUNK_ROWS, SAMPLE_ROW_LIMIT - copied)
            where = "" if last_key is None else f"WHERE {primary_key} > %s "
            try:
                log.execute(gate, access.gate_user,
                            f"INSERT INTO {shadow_table} SELECT * FROM {SCHEMA_NAME}.{change.table} "
                            f"{where}ORDER BY {primary_key} LIMIT {int(chunk)}",
                            None if last_key is None else (last_key,))
            except pymysql.MySQLError as copy_error:
                code = copy_error.args[0] if copy_error.args else None
                abandoned = f"copying the sample failed (MySQL error {code})"
                break
            new_total = int(log.execute(gate, access.gate_user,
                                        f"SELECT COUNT(*) FROM {shadow_table}")[0][0])
            if new_total == copied:
                break  # the table has fewer rows than the limit
            copied = new_total
            last_key = log.execute(gate, access.gate_user,
                                   f"SELECT MAX({primary_key}) FROM {shadow_table}")[0][0]
            time.sleep(PAUSE_BETWEEN_CHUNKS_SECONDS)

        if abandoned is None:
            started_at = time.monotonic()
            try:
                log.execute(gate, access.gate_user, render_shadow_create(change, SHADOW_SCHEMA))
                online = True
                build_seconds = time.monotonic() - started_at
            except pymysql.MySQLError as build_error:
                code = build_error.args[0] if build_error.args else None
                # 1845/1846: ALGORITHM or LOCK not supported for this operation
                # (from MySQL's error list, not reproduced in the lab).
                online = False if code in (1845, 1846) else None
                abandoned = f"building the index on the sample failed (MySQL error {code})"

        if target_query is not None and online:
            refusal = target_query_refusal(target_query, change.table)
            if refusal is not None:
                query_note = f"target query not explained: {refusal}"
            else:
                def open_shadow_reader(database, read_timeout):
                    return _connect(access, access.gate_user, database=SHADOW_SCHEMA,
                                    read_timeout=read_timeout)

                # Read-only session, timeout, and its own session ended if it
                # hangs: the same safeguards as the diagnosis side's EXPLAIN.
                explained = explain_statement(open_shadow_reader, target_query, SHADOW_SCHEMA)
                log.entries.append((access.gate_user, "EXPLAIN " + target_query.strip().rstrip(";")))
                if explained.status != EXPLAINED:
                    query_note = f"target query not explained: {explained.reason}"
                else:
                    keys = [str(row.key) for row in explained.plan]
                    uses_index = change.index_name in keys
                    query_note = ("the target query uses the new index on the sample" if uses_index
                                  else "the target query does not use the new index on the sample "
                                       f"(keys chosen: {', '.join(keys) or 'none'})")
    finally:
        try:
            _drop_shadow(access, log, shadow_table, gate)
        finally:
            gate.close()
            diag.close()
    return Rehearsal(completed=abandoned is None, sample_rows=copied,
                     online_build_possible=online, build_seconds=build_seconds,
                     query_uses_index=uses_index, query_note=query_note, abandoned_reason=abandoned)


def make_plan(access, request, target_query=None, log=None):
    """Validates the request, rehearses it and assesses its risk. Changes nothing in shop."""
    log = log or StatementLog()
    try:
        diag = _connect(access, access.diag_user)
        try:
            schema = read_schema(diag, log, access.diag_user)
            change = validate_request(request, schema)
        finally:
            diag.close()
        rehearsal = rehearse(access, log, change, schema, target_query)
        diag = _connect(access, access.diag_user)
        try:
            risk = assess_risk(diag, log, access.diag_user, change, rehearsal)
        finally:
            diag.close()
    except pymysql.MySQLError as database_error:
        code = database_error.args[0] if database_error.args else None
        raise GateError(f"planning stopped on a database error (MySQL error {code}); "
                        "nothing in shop was changed") from None
    return Plan(change=change, statement=render_create(change), rollback=render_rollback(change),
                rehearsal=rehearsal, risk=risk,
                limits_note=REHEARSAL_LIMITS_NOTE.format(limit=SAMPLE_ROW_LIMIT))


def required_confirmation(plan):
    """What the operator must type: 'yes' for low risk, the index name for high risk."""
    if plan.risk.level == "blocked":
        return None
    return plan.change.index_name if plan.risk.level == "high" else "yes"


def apply_plan(access, plan, typed_confirmation, log=None):
    """Runs the rendered CREATE INDEX if, and only if, the confirmation matches."""
    log = log or StatementLog()
    expected = required_confirmation(plan)
    if expected is None:
        raise GateError("this change is blocked: " + "; ".join(plan.risk.reasons))
    if typed_confirmation != expected:
        raise GateError("confirmation did not match; nothing was changed")
    # No client read timeout: giving up on the client would not stop an
    # online build on the server, and the gate would then misreport the state.
    gate = _connect(access, access.gate_user, read_timeout=None)
    try:
        log.execute(gate, access.gate_user, f"SET SESSION lock_wait_timeout = {APPLY_LOCK_WAIT_SECONDS}")
        try:
            log.execute(gate, access.gate_user, render_create(plan.change))
        except pymysql.MySQLError as apply_error:
            code = apply_error.args[0] if apply_error.args else None
            if code == 1205:
                raise GateError(
                    f"gave up after {APPLY_LOCK_WAIT_SECONDS} s waiting for a metadata lock; "
                    "nothing was changed and no traffic was left queued"
                ) from None
            if code in (2006, 2013):
                raise GateError(
                    f"the connection was lost during the change (error {code}); the outcome is "
                    f"unknown. Check whether the index exists; if it does, undo with: "
                    f"{render_rollback(plan.change)}"
                ) from None
            raise GateError(f"MySQL refused the change (error {code}); nothing was changed") from None
    finally:
        gate.close()
    diag = _connect(access, access.diag_user)
    try:
        schema = read_schema(diag, log, access.diag_user)
    finally:
        diag.close()
    if schema[plan.change.table].indexes.get(plan.change.index_name) != plan.change.columns:
        raise GateError("the index is not present after apply; check the server")
    return render_rollback(plan.change)


def rollback(access, plan, log=None):
    log = log or StatementLog()
    gate = _connect(access, access.gate_user)
    try:
        log.execute(gate, access.gate_user, f"SET SESSION lock_wait_timeout = {APPLY_LOCK_WAIT_SECONDS}")
        log.execute(gate, access.gate_user, render_rollback(plan.change))
    finally:
        gate.close()


def format_plan(plan):
    rehearsal = plan.rehearsal
    lines = [
        "CHANGE SAFETY GATE PLAN",
        f"Change:   {plan.statement}",
        f"Rollback: {plan.rollback}",
        "",
        f"Rehearsal: {'completed' if rehearsal.completed else 'NOT completed'} on "
        f"{rehearsal.sample_rows} sample rows in {SHADOW_SCHEMA}",
        f"  online build (INPLACE, LOCK=NONE): {rehearsal.online_build_possible}",
        f"  build time on the sample: "
        + ("n/a" if rehearsal.build_seconds is None else f"{rehearsal.build_seconds:.2f} s"),
        f"  {rehearsal.query_note}",
    ]
    if rehearsal.abandoned_reason:
        lines.append(f"  stopped: {rehearsal.abandoned_reason}")
    lines += ["", f"Risk: {plan.risk.level.upper()}"]
    lines += [f"  - {reason}" for reason in plan.risk.reasons]
    lines += ["", plan.limits_note, ""]
    expected = required_confirmation(plan)
    if expected is None:
        lines.append("This change is BLOCKED and will not be applied.")
    elif expected == "yes":
        lines.append("To apply, confirm by typing: yes")
    else:
        lines.append(f"HIGH RISK. To apply anyway, type the index name: {expected}")
    return "\n".join(lines)
