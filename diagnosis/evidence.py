"""A read-only snapshot of the lab for the baseline rule engine.

Every statement sent to MySQL here is a fixed SELECT on performance_schema or
information_schema. Nothing captured from the server is ever executed, and no
EXPLAIN is run; that belongs to the evidence collectors of step C1.
"""

import datetime
import time
from dataclasses import dataclass
from typing import Optional

import pymysql
import requests

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 15
PROMETHEUS_TIMEOUT_SECONDS = 5
ONSET_LOOKBACK_SECONDS = 600
ONSET_STEP_SECONDS = 5


@dataclass(frozen=True)
class StatementLoad:
    """What one statement shape did during the observation window."""

    digest_text: str
    sample_text: Optional[str]
    executions: int
    executions_per_second: float
    rows_examined_per_execution: float
    rows_examined_per_second: float
    no_index_used: bool
    average_latency_ms: float


@dataclass(frozen=True)
class SessionEvidence:
    total: int
    max_connections: int
    idle: int
    running: int  # executing a statement and not waiting for any lock
    metadata_lock_waiters: int
    row_lock_waiters: int
    # (rows locked, holder is idle) for each transaction others are waiting on
    blocking_transactions: tuple


@dataclass(frozen=True)
class MetricsEvidence:
    exporter_reaches_mysql: Optional[bool]
    connections_now: Optional[float]
    connections_last_known: Optional[float]
    max_connections: Optional[float]
    connections_change_per_second: Optional[float]
    # Metrics that should be present and are not. An absent metric is evidence
    # of an anomaly, never of health.
    missing: tuple


@dataclass(frozen=True)
class Evidence:
    connection_path: str  # "normal", "admin" or "none"
    normal_port_error_code: Optional[int]
    admin_port_tried: bool
    admin_port_error_code: Optional[int]
    metrics: MetricsEvidence
    sessions: Optional[SessionEvidence]
    statements: tuple
    onset: Optional[str]


def _prometheus_value(prometheus_url, promql):
    try:
        response = requests.get(
            f"{prometheus_url}/api/v1/query", params={"query": promql},
            timeout=PROMETHEUS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        samples = response.json()["data"]["result"]
    except (requests.RequestException, ValueError, KeyError):
        return None
    return float(samples[0]["value"][1]) if samples else None


def _prometheus_series(prometheus_url, promql, end_time):
    try:
        response = requests.get(
            f"{prometheus_url}/api/v1/query_range",
            params={"query": promql, "start": end_time - ONSET_LOOKBACK_SECONDS,
                    "end": end_time, "step": ONSET_STEP_SECONDS},
            timeout=PROMETHEUS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        series = response.json()["data"]["result"]
    except (requests.RequestException, ValueError, KeyError):
        return []
    return [(float(timestamp), float(value)) for timestamp, value in series[0]["values"]] if series else []


def collect_metrics(prometheus_url):
    exporter_value = _prometheus_value(prometheus_url, "mysql_up")
    connections_now = _prometheus_value(prometheus_url, "mysql_global_status_threads_connected")
    missing = []
    if exporter_value is None:
        missing.append("mysql_up")
    if connections_now is None:
        missing.append("mysql_global_status_threads_connected")
    return MetricsEvidence(
        exporter_reaches_mysql=None if exporter_value is None else exporter_value == 1,
        connections_now=connections_now,
        connections_last_known=_prometheus_value(
            prometheus_url, "last_over_time(mysql_global_status_threads_connected[5m])"
        ),
        max_connections=_prometheus_value(
            prometheus_url, "last_over_time(mysql_global_variables_max_connections[5m])"
        ),
        connections_change_per_second=_prometheus_value(
            prometheus_url, "deriv(mysql_global_status_threads_connected[1m])"
        ),
        missing=tuple(missing),
    )


def first_sustained_anomaly(points, is_abnormal, sustained_share=0.8):
    """Returns the timestamp from which a series stays abnormal to its end.

    points is a list of (timestamp, value). The answer is the earliest point
    that is abnormal and after which at least sustained_share of the points
    are abnormal too. Returns None if the series does not end abnormal.
    """
    if not points or not is_abnormal(points[-1][1]):
        return None
    abnormal_flags = [is_abnormal(value) for _, value in points]
    abnormal_after = 0
    best_timestamp = None
    for position in range(len(points) - 1, -1, -1):
        abnormal_after += abnormal_flags[position]
        remaining = len(points) - position
        if abnormal_flags[position] and abnormal_after / remaining >= sustained_share:
            best_timestamp = points[position][0]
    return best_timestamp


def estimate_onset(prometheus_url, end_time=None):
    """Estimates when the current anomaly started, from metric history.

    Looks at connection count, workload errors, workload latency and exporter
    reachability over the last ten minutes, and takes the earliest point from
    which any of them stays abnormal. Returns ISO 8601 UTC, or None.
    """
    end_time = end_time or time.time()

    def baseline_of(points):
        early_values = sorted(value for _, value in points[: max(3, len(points) // 3)])
        return early_values[len(early_values) // 2] if early_values else 0.0

    candidates = []
    connections = _prometheus_series(prometheus_url, "mysql_global_status_threads_connected", end_time)
    if connections:
        usual_connections = baseline_of(connections)
        candidates.append(first_sustained_anomaly(connections, lambda v: v > usual_connections + 4))
    errors = _prometheus_series(
        prometheus_url,
        'sum(rate(workload_operations_total{outcome="error"}[15s])) or vector(0)', end_time,
    )
    candidates.append(first_sustained_anomaly(errors, lambda v: v > 0))
    latency = _prometheus_series(
        prometheus_url,
        "sum(rate(workload_operation_duration_seconds_sum[15s])) / "
        "sum(rate(workload_operation_duration_seconds_count[15s]))", end_time,
    )
    if latency:
        usual_latency = baseline_of(latency)
        candidates.append(
            first_sustained_anomaly(latency, lambda v: v > max(3 * usual_latency, 0.01))
        )
    exporter = _prometheus_series(prometheus_url, "mysql_up", end_time)
    candidates.append(first_sustained_anomaly(exporter, lambda v: v == 0))

    found = [timestamp for timestamp in candidates if timestamp is not None]
    if not found:
        return None
    return datetime.datetime.fromtimestamp(min(found), datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


def _connect(access, port):
    return pymysql.connect(
        host=access.mysql_host, port=port, user=access.mysql_user,
        password=access.mysql_password, autocommit=True,
        connect_timeout=CONNECT_TIMEOUT_SECONDS, read_timeout=READ_TIMEOUT_SECONDS,
    )


def open_diagnosis_connection(access):
    """Tries the normal port, then the administrative port if there is one.

    Returns (connection or None, path, normal error code, admin tried, admin
    error code).
    """
    try:
        return _connect(access, access.mysql_port), "normal", None, False, None
    except pymysql.MySQLError as normal_error:
        normal_error_code = normal_error.args[0] if normal_error.args else None
    if access.mysql_admin_port is None:
        return None, "none", normal_error_code, False, None
    try:
        return _connect(access, access.mysql_admin_port), "admin", normal_error_code, True, None
    except pymysql.MySQLError as admin_error:
        admin_error_code = admin_error.args[0] if admin_error.args else None
        return None, "none", normal_error_code, True, admin_error_code


def _fetch_value(connection, sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone()[0]


SESSION_FILTER = (
    "USER NOT IN ('event_scheduler', 'system user') AND ID <> CONNECTION_ID()"
)


def collect_sessions(connection):
    def count_sessions(extra_condition):
        return int(_fetch_value(
            connection,
            f"SELECT COUNT(*) FROM performance_schema.processlist "
            f"WHERE {SESSION_FILTER} AND {extra_condition}",
        ))

    metadata_lock_waiters = count_sessions("STATE = 'Waiting for table metadata lock'")
    row_lock_waiters = int(_fetch_value(
        connection,
        "SELECT COUNT(DISTINCT REQUESTING_THREAD_ID) FROM performance_schema.data_lock_waits",
    ))
    executing = count_sessions("COMMAND = 'Query'")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT blocking_trx.trx_id, blocking_trx.trx_rows_locked, session.COMMAND "
            "FROM performance_schema.data_lock_waits AS lock_wait "
            "JOIN information_schema.INNODB_TRX AS blocking_trx "
            "  ON blocking_trx.trx_id = lock_wait.BLOCKING_ENGINE_TRANSACTION_ID "
            "LEFT JOIN performance_schema.processlist AS session "
            "  ON session.ID = blocking_trx.trx_mysql_thread_id"
        )
        blocking_transactions = tuple(
            (int(rows_locked), command == "Sleep") for _, rows_locked, command in cursor.fetchall()
        )
    return SessionEvidence(
        total=count_sessions("1 = 1"),
        max_connections=int(_fetch_value(connection, "SELECT @@global.max_connections")),
        idle=count_sessions("COMMAND = 'Sleep'"),
        running=max(0, executing - metadata_lock_waiters - row_lock_waiters),
        metadata_lock_waiters=metadata_lock_waiters,
        row_lock_waiters=row_lock_waiters,
        blocking_transactions=blocking_transactions,
    )


def _digest_snapshot(connection):
    with connection.cursor() as cursor:
        # Zero-count rows are ignored (docs/KNOWN_ISSUES.md). Statements about
        # the server's own schemas are this tool's and the exporter's.
        cursor.execute(
            "SELECT DIGEST, DIGEST_TEXT, QUERY_SAMPLE_TEXT, COUNT_STAR, SUM_ROWS_EXAMINED, "
            "SUM_NO_INDEX_USED, SUM_TIMER_WAIT "
            "FROM performance_schema.events_statements_summary_by_digest "
            "WHERE COUNT_STAR > 0 AND DIGEST IS NOT NULL "
            "AND DIGEST_TEXT NOT LIKE '%performance_schema%' "
            "AND DIGEST_TEXT NOT LIKE '%information_schema%' "
            "AND DIGEST_TEXT NOT LIKE 'SHOW %' AND DIGEST_TEXT NOT LIKE 'SET %'"
        )
        return {row[0]: row for row in cursor.fetchall()}


def statement_loads(before, after, window_seconds):
    """Per-statement activity between two digest snapshots, busiest first."""
    loads = []
    for digest, after_row in after.items():
        before_row = before.get(digest)
        executions = int(after_row[3]) - (int(before_row[3]) if before_row else 0)
        if executions <= 0:
            continue
        rows_examined = int(after_row[4]) - (int(before_row[4]) if before_row else 0)
        without_index = int(after_row[5]) - (int(before_row[5]) if before_row else 0)
        picoseconds = int(after_row[6]) - (int(before_row[6]) if before_row else 0)
        loads.append(StatementLoad(
            digest_text=after_row[1],
            sample_text=after_row[2],
            executions=executions,
            executions_per_second=executions / window_seconds,
            rows_examined_per_execution=rows_examined / executions,
            rows_examined_per_second=rows_examined / window_seconds,
            no_index_used=without_index > 0,
            average_latency_ms=picoseconds / executions / 1e9,
        ))
    return tuple(sorted(loads, key=lambda load: load.rows_examined_per_second, reverse=True))


def collect_evidence(access, window_seconds=5):
    """Takes one snapshot of the lab. Takes a little over window_seconds."""
    metrics = collect_metrics(access.prometheus_url)
    connection, path, normal_error_code, admin_tried, admin_error_code = (
        open_diagnosis_connection(access)
    )
    sessions, statements = None, ()
    if connection is not None:
        try:
            digests_before = _digest_snapshot(connection)
            started_at = time.monotonic()
            time.sleep(window_seconds)
            digests_after = _digest_snapshot(connection)
            statements = statement_loads(
                digests_before, digests_after, time.monotonic() - started_at
            )
            sessions = collect_sessions(connection)
        finally:
            connection.close()
    return Evidence(
        connection_path=path,
        normal_port_error_code=normal_error_code,
        admin_port_tried=admin_tried,
        admin_port_error_code=admin_error_code,
        metrics=metrics,
        sessions=sessions,
        statements=statements,
        onset=estimate_onset(access.prometheus_url),
    )
