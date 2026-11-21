"""The rule engine, on hand-built evidence. One test per rule, plus the cases
where rules must NOT fire."""

import pytest

from diagnosis.evidence import (
    Evidence,
    MetricsEvidence,
    SessionEvidence,
    StatementLoad,
    first_sustained_anomaly,
)
from diagnosis.rules import decide, suggest_index

GOOD_METRICS = MetricsEvidence(
    exporter_reaches_mysql=True, connections_now=5.0, connections_last_known=5.0,
    max_connections=60.0, connections_change_per_second=0.0, missing=(),
)
QUIET_SESSIONS = SessionEvidence(
    total=5, max_connections=60, idle=5, running=0, metadata_lock_waiters=0,
    row_lock_waiters=0, blocking_transactions=(),
)
INDEXED_STATEMENT = StatementLoad(
    digest_text="SELECT ... FROM `orders` WHERE `id` = ?", sample_text="SELECT id FROM orders WHERE id = 7",
    executions=30, executions_per_second=6.0, rows_examined_per_execution=1.0,
    rows_examined_per_second=6.0, no_index_used=False, average_latency_ms=0.5,
)
CUSTOMER_SCAN = StatementLoad(
    digest_text="SELECT ... FROM `orders` WHERE `customer_id` = ? ORDER BY `created_at` DESC LIMIT ?",
    sample_text=("SELECT id, status, total_cents, created_at FROM orders "
                 "WHERE customer_id = 90 ORDER BY created_at DESC LIMIT 10"),
    executions=25, executions_per_second=5.0, rows_examined_per_execution=200_000.0,
    rows_examined_per_second=1_000_000.0, no_index_used=True, average_latency_ms=30.0,
)
RARE_REPORT = StatementLoad(
    digest_text="SELECT `products` . `category` , COUNT ( * ) ...",
    sample_text="SELECT products.category, COUNT(*) FROM shop.order_items JOIN shop.products ...",
    executions=1, executions_per_second=0.2, rows_examined_per_execution=800_000.0,
    rows_examined_per_second=160_000.0, no_index_used=True, average_latency_ms=320.0,
)


def evidence(sessions=QUIET_SESSIONS, statements=(INDEXED_STATEMENT,), metrics=GOOD_METRICS,
             path="normal", normal_error=None, admin_tried=False, admin_error=None):
    return Evidence(
        connection_path=path, normal_port_error_code=normal_error, admin_port_tried=admin_tried,
        admin_port_error_code=admin_error, metrics=metrics, sessions=sessions,
        statements=tuple(statements), onset="2026-11-21T10:00:00+00:00",
    )


def sessions(**changes):
    return SessionEvidence(**{**QUIET_SESSIONS.__dict__, **changes})


def metrics(**changes):
    return MetricsEvidence(**{**GOOD_METRICS.__dict__, **changes})


def test_quiet_lab_is_healthy_and_says_why():
    diagnosis = decide(evidence())

    assert diagnosis.root_cause == "healthy"
    assert diagnosis.symptoms == ()
    assert "lock" in diagnosis.rationale and "index" in diagnosis.rationale


def test_busy_but_fast_is_healthy():
    busy = sessions(total=14, idle=10, running=4)

    assert decide(evidence(busy, [INDEXED_STATEMENT] * 3)).root_cause == "healthy"


def test_rare_slow_report_is_not_a_missing_index():
    # It reads the whole table and uses no index, but it ran once.
    assert decide(evidence(statements=[RARE_REPORT, INDEXED_STATEMENT])).root_cause == "healthy"


def test_frequent_scan_is_a_missing_index_with_an_index_suggestion():
    diagnosis = decide(evidence(statements=[CUSTOMER_SCAN, INDEXED_STATEMENT]))

    assert diagnosis.root_cause == "missing_index_slow_query"
    assert diagnosis.index_suggestion.table == "orders"
    assert diagnosis.index_suggestion.columns == ("customer_id", "created_at")
    assert "1,000,000" in diagnosis.rationale
    assert any("200000 rows" in fact for fact in diagnosis.evidence)


def test_a_scan_below_the_load_threshold_is_only_a_weak_signal():
    light_scan = StatementLoad(**{**CUSTOMER_SCAN.__dict__, "executions": 3,
                                  "executions_per_second": 0.6,
                                  "rows_examined_per_second": 120_000.0})

    assert decide(evidence(statements=[light_scan])).root_cause == "undetermined"


def test_two_row_lock_waiters_is_a_lock_wait_one_is_not_enough():
    blocked = sessions(total=6, idle=2, row_lock_waiters=3,
                       blocking_transactions=((50_000, True),))
    one_waiter = sessions(total=6, idle=4, row_lock_waiters=1,
                          blocking_transactions=((1, False),))

    lock_diagnosis = decide(evidence(blocked))

    assert lock_diagnosis.root_cause == "lock_wait"
    assert any("50000 row locks" in fact and "idle" in fact for fact in lock_diagnosis.evidence)
    assert decide(evidence(one_waiter)).root_cause == "undetermined"


def test_three_metadata_lock_waiters_is_a_lock_wait_and_is_described_as_such():
    queued = sessions(total=8, idle=2, metadata_lock_waiters=5)

    diagnosis = decide(evidence(queued))

    assert diagnosis.root_cause == "lock_wait"
    assert "metadata lock" in diagnosis.rationale and "schema change" in diagnosis.rationale
    assert decide(evidence(sessions(total=6, metadata_lock_waiters=2))).root_cause == "undetermined"


def test_full_and_mostly_idle_is_connection_exhaustion():
    flooded = sessions(total=60, idle=57, running=1)

    diagnosis = decide(evidence(flooded, path="admin", normal_error=1040, admin_tried=True))

    assert diagnosis.root_cause == "connection_exhaustion"
    assert diagnosis.symptoms == ()
    assert "administrative port" in diagnosis.evidence[0]


def test_full_because_of_lock_waits_names_the_lock_and_lists_exhaustion_as_symptom():
    piled_up = sessions(total=60, idle=3, running=1, row_lock_waiters=56,
                        blocking_transactions=((50_000, True),))

    diagnosis = decide(evidence(piled_up, path="admin", normal_error=1040, admin_tried=True))

    assert diagnosis.root_cause == "lock_wait"
    assert diagnosis.symptoms == ("connection_exhaustion",)
    assert "consequence" in diagnosis.rationale


def test_full_because_of_scans_names_the_index_and_lists_exhaustion_as_symptom():
    scanning = sessions(total=60, idle=5, running=50)
    report_scan = StatementLoad(
        digest_text="SELECT ... FROM `shop` . `order_items` WHERE `product_id` = ? AND `quantity` >= ?",
        sample_text=("SELECT order_items.order_id FROM shop.order_items WHERE "
                     "order_items.product_id = 411 AND order_items.quantity >= 2 "
                     "ORDER BY order_items.unit_price_cents DESC LIMIT 50"),
        executions=90, executions_per_second=18.0, rows_examined_per_execution=400_000.0,
        rows_examined_per_second=7_200_000.0, no_index_used=True, average_latency_ms=2500.0,
    )

    diagnosis = decide(evidence(scanning, [report_scan], path="admin", normal_error=1040,
                                admin_tried=True))

    assert diagnosis.root_cause == "missing_index_slow_query"
    assert diagnosis.symptoms == ("connection_exhaustion",)
    assert diagnosis.index_suggestion.table == "order_items"
    assert diagnosis.index_suggestion.columns[0] == "product_id"


def test_full_with_no_majority_explanation_is_undetermined_not_a_guess():
    mixed = sessions(total=58, idle=20, running=18, row_lock_waiters=20)

    diagnosis = decide(evidence(mixed, path="admin", normal_error=1040, admin_tried=True))

    assert diagnosis.root_cause == "undetermined"
    assert diagnosis.symptoms == ("connection_exhaustion",)


def test_idle_sessions_still_climbing_is_a_leak_before_the_limit_is_reached():
    leaking = sessions(total=24, idle=23)

    diagnosis = decide(evidence(leaking, metrics=metrics(connections_change_per_second=1.0)))

    assert diagnosis.root_cause == "connection_exhaustion"
    assert "climbing" in diagnosis.rationale


def test_a_few_extra_sessions_climbing_is_only_a_weak_signal():
    early_leak = sessions(total=12, idle=11)

    diagnosis = decide(evidence(early_leak, metrics=metrics(connections_change_per_second=1.0)))

    assert diagnosis.root_cause == "undetermined"
    assert "only just started" in diagnosis.rationale


def test_no_connection_and_no_admin_port_is_undetermined_with_the_outside_symptom():
    gone = metrics(exporter_reaches_mysql=False, connections_now=None,
                   missing=("mysql_global_status_threads_connected",))

    diagnosis = decide(evidence(sessions=None, statements=(), metrics=gone, path="none",
                                normal_error=1040))

    assert diagnosis.root_cause == "undetermined"
    assert diagnosis.symptoms == ("connection_exhaustion",)
    assert "No administrative port" in diagnosis.rationale
    assert any("disappeared" in fact for fact in diagnosis.evidence)


def test_no_connection_for_another_reason_claims_no_exhaustion():
    diagnosis = decide(evidence(sessions=None, statements=(), path="none", normal_error=2003))

    assert diagnosis.root_cause == "undetermined"
    assert diagnosis.symptoms == ()


@pytest.mark.parametrize(
    "broken_metrics",
    [
        metrics(exporter_reaches_mysql=False),
        metrics(exporter_reaches_mysql=None, missing=("mysql_up",)),
        metrics(connections_now=None, missing=("mysql_global_status_threads_connected",)),
    ],
)
def test_missing_metrics_are_never_read_as_healthy(broken_metrics):
    # The database looks quiet from inside, but its metrics have gone.
    diagnosis = decide(evidence(metrics=broken_metrics))

    assert diagnosis.root_cause != "healthy"
    assert diagnosis.root_cause == "undetermined"


@pytest.mark.parametrize("refusal_code", [1040, 2003])
def test_a_refused_normal_port_is_never_healthy_even_if_all_looks_quiet_inside(refusal_code):
    # Reached only through the administrative port, and by then nothing shows.
    diagnosis = decide(evidence(path="admin", normal_error=refusal_code, admin_tried=True))

    assert diagnosis.root_cause == "undetermined"
    assert any(str(refusal_code) in fact for fact in diagnosis.evidence)


@pytest.mark.parametrize(
    "sample_text, expected",
    [
        ("SELECT id FROM orders WHERE customer_id = 90 ORDER BY created_at DESC LIMIT 10",
         ("orders", ("customer_id", "created_at"))),
        ("SELECT order_items.order_id FROM shop.order_items WHERE order_items.product_id = 411 "
         "AND order_items.quantity >= 2 ORDER BY order_items.unit_price_cents DESC LIMIT 50",
         ("order_items", ("product_id", "quantity"))),
        ("SELECT id FROM `shop`.`orders` WHERE `status` = 'paid'", ("orders", ("status",))),
        ("SELECT id FROM orders WHERE total_cents > 100", ("orders", ("total_cents",))),
    ],
)
def test_index_suggestion_puts_equality_columns_first(sample_text, expected):
    suggestion = suggest_index(sample_text)

    assert (suggestion.table, suggestion.columns) == expected


@pytest.mark.parametrize(
    "sample_text",
    [None, "", "SELECT COUNT(*) FROM orders",
     "SELECT a.id FROM orders a JOIN order_items b ON b.order_id = a.id WHERE a.status = 'x'",
     "UPDATE orders SET status = 'x'"],
)
def test_index_suggestion_declines_when_it_cannot_tell(sample_text):
    assert suggest_index(sample_text) is None


def test_onset_is_the_start_of_the_final_abnormal_stretch():
    points = [(t, 5.0) for t in range(0, 40, 5)] + [(40, 30.0)] \
        + [(t, 5.0) for t in range(45, 60, 5)] + [(t, 40.0) for t in range(60, 100, 5)]

    # The blip at 40 recovered for three samples; the sustained anomaly starts
    # at 60. (From 40 onwards only 9 of 12 samples are abnormal, under the 80%
    # needed, so the blip is not taken as the start.)
    assert first_sustained_anomaly(points, lambda value: value > 9) == 60


def test_onset_tolerates_one_normal_sample_inside_the_abnormal_stretch():
    points = [(t, 5.0) for t in range(0, 30, 5)] + [(30, 40.0), (35, 5.0)] \
        + [(t, 40.0) for t in range(40, 100, 5)]

    assert first_sustained_anomaly(points, lambda value: value > 9) == 30


def test_onset_is_none_when_the_series_ends_normal_or_is_empty():
    recovered = [(0, 5.0), (5, 40.0), (10, 40.0), (15, 5.0)]

    assert first_sustained_anomaly(recovered, lambda value: value > 9) is None
    assert first_sustained_anomaly([], lambda value: value > 9) is None
