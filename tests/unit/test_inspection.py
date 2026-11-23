"""Inspection agent: risk rules on constructed readings, and the schedule."""

from diagnosis import inspection
from diagnosis.evidence import StatementLoad
from diagnosis.inspection import InspectionReadings, LongTransaction, assess_risks


def _flat(value, points=24, start=1_000.0):
    return tuple((start + 25 * index, float(value)) for index in range(points))


def _rising(start_value, per_minute, points=24, start=1_000.0):
    return tuple((start + 25 * index, start_value + per_minute * 25 * index / 60)
                 for index in range(points))


def _statement(no_index_used=True, per_second=2.0, rows_per_execution=50_000):
    return StatementLoad(
        digest_text="SELECT * FROM `order_items` WHERE `product_id` = ?", sample_text=None,
        schema_name="shop", sample_truncated=False, explain=None, executions=20,
        executions_per_second=per_second, rows_examined_per_execution=rows_per_execution,
        rows_examined_per_second=per_second * rows_per_execution, no_index_used=no_index_used,
        average_latency_ms=40.0)


def _readings(points=None, max_connections=60.0, transactions=(), statements=(), reachable=True):
    return InspectionReadings(points if points is not None else _flat(6), max_connections,
                              tuple(transactions), tuple(statements), reachable)


def test_a_quiet_baseline_has_no_risks():
    assert assess_risks(_readings()) == []


def test_high_connection_use_is_a_high_risk():
    risks = assess_risks(_readings(points=_flat(50)))
    assert [(risk.check, risk.severity) for risk in risks] == [("connections", "high")]
    assert "50 of 60" in risks[0].message


def test_a_rising_trend_that_fills_the_server_soon_is_reported_with_the_minutes_left():
    # From 6 to about 36 over ten minutes: 3 a minute, about 8 minutes to 60.
    risks = assess_risks(_readings(points=_rising(6, 3.0)))
    assert [(risk.check, risk.severity) for risk in risks] == [("connections", "medium")]
    assert "rising by 3.0 a minute" in risks[0].message


def test_a_slow_rise_that_would_take_hours_is_not_a_risk():
    assert assess_risks(_readings(points=_rising(6, 0.05))) == []


def test_a_missing_connection_metric_is_a_risk_not_a_clear():
    risks = assess_risks(_readings(points=(), max_connections=None))
    assert [(risk.check, risk.severity) for risk in risks] == [("connections", "high")]
    assert "missing" in risks[0].message


def test_long_transactions_are_graded_by_age_and_locked_rows():
    risks = assess_risks(_readings(transactions=[
        LongTransaction(1, 30, 5, False),        # too young: not listed
        LongTransaction(2, 90, 10, False),       # medium
        LongTransaction(3, 120, 50_000, True),   # high: many locked rows
        LongTransaction(4, 400, 0, True),        # high: very old
    ]))
    listed = {risk.message.split()[1]: risk.severity for risk in risks}
    assert listed == {"2": "medium", "3": "high", "4": "high"}
    assert risks[0].severity == "high" and risks[-1].severity == "medium"
    assert "session is idle" in next(risk.message for risk in risks if risk.message.split()[1] == "3")


def test_only_frequent_heavy_unindexed_statements_are_risks():
    risks = assess_risks(_readings(statements=[
        _statement(),                                  # high: 100,000 rows a second
        _statement(no_index_used=False),               # uses an index
        _statement(per_second=0.1),                    # rare
        _statement(rows_per_execution=10),             # cheap
        _statement(per_second=1.0, rows_per_execution=5_000),  # medium
    ]))
    assert [(risk.check, risk.severity) for risk in risks] == [
        ("unindexed_statement", "high"), ("unindexed_statement", "medium")]
    assert "[captured SQL, untrusted]" in risks[0].message


def test_an_unreachable_database_is_reported():
    risks = assess_risks(_readings(reachable=False))
    assert risks[0].check == "inspection" and risks[0].severity == "high"


def test_the_report_lists_risks_only_and_never_a_root_cause():
    text = inspection.render_risks(assess_risks(_readings(points=_flat(55))), "2026-11-22T00:00:00+00:00")
    assert "[HIGH" in text
    for root_cause in ("missing_index_slow_query", "lock_wait", "connection_exhaustion", "ROOT CAUSE"):
        assert root_cause not in text
    assert "no risks found" in inspection.render_risks([], "t")


def test_the_schedule_repeats_at_the_interval(monkeypatch):
    waits, printed = [], []
    monkeypatch.setattr(inspection, "load_diagnosis_access", lambda admin_port_available: None)
    monkeypatch.setattr(inspection, "inspect_once", lambda access: printed.append("run") or "INSPECTION")
    assert inspection.main(["--every", "60", "--count", "3"], sleep=waits.append) == 0
    assert printed == ["run", "run", "run"] and waits == [60, 60]


def test_the_interval_cannot_be_too_short():
    import pytest
    with pytest.raises(SystemExit):
        inspection.main(["--every", "5"])
