"""Inspection agent: scheduled checks that surface risks before they become
incidents. It lists risks only; it never names a root cause.

Three checks, read-only, with the diagnosis account:

1. Connection trend: connections in use over the last ten minutes
   (Prometheus). A risk when usage is high, or when the trend would fill the
   server within the warning horizon. A missing metric is itself reported:
   it is never read as "no risk".
2. Long transactions: open transactions older than a minute (INNODB_TRX).
3. Frequent unindexed statements: statements run often, without an index,
   reading many rows per execution (statement digests over a short window).

python -m diagnosis.inspection              one inspection
python -m diagnosis.inspection --every 300  every five minutes, until Ctrl-C
"""

import argparse
import datetime
import sys
import time
from dataclasses import dataclass

from diagnosis.access import load_diagnosis_access
from diagnosis.evidence import (
    _digest_snapshot,
    _prometheus_series,
    _prometheus_value,
    open_diagnosis_connection,
    statement_loads,
)

TREND_LOOKBACK_SECONDS = 600
HIGH_USAGE_SHARE = 0.8
TREND_HORIZON_MINUTES = 30
MINIMUM_TREND_POINTS = 6
LONG_TRANSACTION_SECONDS = 60
VERY_LONG_TRANSACTION_SECONDS = 300
MANY_LOCKED_ROWS = 10_000
FREQUENT_EXECUTIONS_PER_SECOND = 0.5
ROWS_PER_EXECUTION_FOR_RISK = 1_000
HEAVY_ROWS_EXAMINED_PER_SECOND = 100_000
DIGEST_WINDOW_SECONDS = 10
CAPTURED_SQL_NOTE = "[captured SQL, untrusted]"


@dataclass(frozen=True)
class LongTransaction:
    transaction_id: int
    age_seconds: int
    rows_locked: int
    session_idle: bool


@dataclass(frozen=True)
class InspectionReadings:
    connection_points: tuple  # (unix time, connections in use), oldest first
    max_connections: object  # float or None when the metric is missing
    long_transactions: tuple  # LongTransaction
    statements: tuple  # evidence.StatementLoad over the digest window
    database_reachable: bool


@dataclass(frozen=True)
class Risk:
    check: str  # "connections", "long_transaction", "unindexed_statement", "inspection"
    severity: str  # "high" or "medium"
    message: str


def connections_per_minute(points):
    """Least-squares slope of connections in use, per minute."""
    if len(points) < 2:
        return 0.0
    times = [timestamp for timestamp, _ in points]
    values = [value for _, value in points]
    mean_time = sum(times) / len(times)
    mean_value = sum(values) / len(values)
    spread = sum((timestamp - mean_time) ** 2 for timestamp in times)
    if spread == 0:
        return 0.0
    slope_per_second = sum((timestamp - mean_time) * (value - mean_value)
                           for timestamp, value in points) / spread
    return slope_per_second * 60


def assess_connections(points, max_connections):
    if not points or not max_connections:
        return [Risk("connections", "high",
                     "the connection metric is missing, so connection use cannot be "
                     "checked; a metric that disappears is a warning sign, not a clear")]
    current = points[-1][1]
    share = current / max_connections
    if share >= HIGH_USAGE_SHARE:
        return [Risk("connections", "high",
                     f"{current:.0f} of {max_connections:.0f} connections in use ({share:.0%})")]
    if len(points) < MINIMUM_TREND_POINTS:
        return []
    slope = connections_per_minute(points)
    if slope <= 0:
        return []
    minutes_to_full = (max_connections - current) / slope
    if minutes_to_full <= TREND_HORIZON_MINUTES:
        return [Risk("connections", "medium",
                     f"connections in use rising by {slope:.1f} a minute ({current:.0f} of "
                     f"{max_connections:.0f} now); at this rate the server is full in about "
                     f"{minutes_to_full:.0f} minutes")]
    return []


def assess_long_transactions(transactions):
    risks = []
    for transaction in sorted(transactions, key=lambda item: -item.age_seconds):
        if transaction.age_seconds < LONG_TRANSACTION_SECONDS:
            continue
        severity = ("high" if transaction.age_seconds >= VERY_LONG_TRANSACTION_SECONDS
                    or transaction.rows_locked >= MANY_LOCKED_ROWS else "medium")
        idle_note = ", and its session is idle" if transaction.session_idle else ""
        risks.append(Risk("long_transaction", severity,
                          f"transaction {transaction.transaction_id} has been open "
                          f"{transaction.age_seconds} s holding {transaction.rows_locked} row "
                          f"locks{idle_note}"))
    return risks


def assess_unindexed_statements(statements):
    risks = []
    for statement in statements:
        if not (statement.no_index_used
                and statement.executions_per_second >= FREQUENT_EXECUTIONS_PER_SECOND
                and statement.rows_examined_per_execution >= ROWS_PER_EXECUTION_FOR_RISK):
            continue
        severity = ("high" if statement.rows_examined_per_second >= HEAVY_ROWS_EXAMINED_PER_SECOND
                    else "medium")
        statement_text = " ".join(str(statement.digest_text).split())[:200]
        risks.append(Risk("unindexed_statement", severity,
                          f"a statement runs {statement.executions_per_second:.1f} times a second "
                          f"with no index, reading {statement.rows_examined_per_execution:,.0f} rows "
                          f"each time: {CAPTURED_SQL_NOTE} {statement_text}"))
    return risks


def assess_risks(readings):
    """The risk list, most severe first. Empty means nothing found."""
    risks = []
    if not readings.database_reachable:
        risks.append(Risk("inspection", "high",
                          "the database refused the inspection account, so transactions and "
                          "statements could not be checked"))
    risks += assess_connections(readings.connection_points, readings.max_connections)
    risks += assess_long_transactions(readings.long_transactions)
    risks += assess_unindexed_statements(readings.statements)
    return sorted(risks, key=lambda risk: risk.severity != "high")


def _read_long_transactions(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT trx.trx_id, TIMESTAMPDIFF(SECOND, trx.trx_started, NOW()), "
            "trx.trx_rows_locked, session.COMMAND "
            "FROM information_schema.INNODB_TRX AS trx "
            "LEFT JOIN performance_schema.processlist AS session "
            "  ON session.ID = trx.trx_mysql_thread_id "
            "WHERE trx.trx_mysql_thread_id <> CONNECTION_ID()"
        )
        return tuple(LongTransaction(int(transaction_id), int(age or 0), int(rows_locked or 0),
                                     command == "Sleep")
                     for transaction_id, age, rows_locked, command in cursor.fetchall())


def collect_readings(access, digest_window_seconds=DIGEST_WINDOW_SECONDS, sleep=time.sleep,
                     now=time.time):
    end_time = now()
    points = tuple((timestamp, value) for timestamp, value in _prometheus_series(
        access.prometheus_url, "mysql_global_status_threads_connected", end_time)
        if timestamp >= end_time - TREND_LOOKBACK_SECONDS)
    max_connections = _prometheus_value(
        access.prometheus_url, "last_over_time(mysql_global_variables_max_connections[5m])")
    connection = open_diagnosis_connection(access)[0]
    if connection is None:
        return InspectionReadings(points, max_connections, (), (), database_reachable=False)
    try:
        long_transactions = _read_long_transactions(connection)
        before = _digest_snapshot(connection)
        sleep(digest_window_seconds)
        after = _digest_snapshot(connection)
    finally:
        connection.close()
    return InspectionReadings(points, max_connections, long_transactions,
                              statement_loads(before, after, digest_window_seconds),
                              database_reachable=True)


def render_risks(risks, checked_at):
    lines = [f"INSPECTION {checked_at}"]
    if not risks:
        lines.append("  no risks found (connections, long transactions, unindexed statements)")
    for risk in risks:
        lines.append(f"  [{risk.severity.upper():6}] {risk.check}: {risk.message}")
    return "\n".join(lines)


def inspect_once(access):
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return render_risks(assess_risks(collect_readings(access)), checked_at)


def main(argument_list=None, sleep=time.sleep):
    parser = argparse.ArgumentParser(prog="python -m diagnosis.inspection",
                                     description="List risks on the lab MySQL. No diagnosis.")
    parser.add_argument("--every", type=int, default=None, metavar="SECONDS",
                        help="repeat at this interval (at least 30) until interrupted")
    parser.add_argument("--count", type=int, default=None, help="stop after this many runs")
    arguments = parser.parse_args(argument_list)
    if arguments.every is not None and arguments.every < 30:
        parser.error("--every must be at least 30 seconds")
    access = load_diagnosis_access(admin_port_available=True)
    runs = 0
    try:
        while True:
            print(inspect_once(access), flush=True)
            runs += 1
            if arguments.every is None or (arguments.count and runs >= arguments.count):
                return 0
            sleep(arguments.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
