"""The metrics agent, on hand-built evidence. No containers.

The agent reads only the metrics side of a snapshot. It reuses the frozen rule
engine's assess_metrics, never names a root cause, and must treat a metric that
has disappeared as a sign of trouble, never as health.
"""

import itertools

import pytest

from diagnosis.agents.base import AgentFinding
from diagnosis.agents.metrics_agent import investigate_metrics
from diagnosis.evidence import Evidence, MetricsEvidence, SessionEvidence
from diagnosis.rules import assess_metrics

ONSET = "2026-11-21T10:00:00+00:00"
HEALTHY_METRICS = MetricsEvidence(
    exporter_reaches_mysql=True, connections_now=5.0, connections_last_known=5.0,
    max_connections=60.0, connections_change_per_second=0.0, missing=(),
)
QUIET_SESSIONS = SessionEvidence(
    total=5, max_connections=60, idle=5, running=0, metadata_lock_waiters=0,
    row_lock_waiters=0, blocking_transactions=(),
)
ALL_ANOMALY_TYPES = (
    "connections_climbing", "connections_near_limit", "exporter_unreachable",
    "metrics_missing", "normal_port_refused",
)


def metrics(**changes):
    return MetricsEvidence(**{**HEALTHY_METRICS.__dict__, **changes})


def snapshot(metrics_evidence=HEALTHY_METRICS, normal_error=None, path="normal",
             sessions=QUIET_SESSIONS, onset=ONSET):
    return Evidence(
        connection_path=path, normal_port_error_code=normal_error, admin_port_tried=False,
        admin_port_error_code=None, metrics=metrics_evidence, sessions=sessions,
        statements=(), onset=onset,
    )


# Each anomaly type, alone, with the change to a healthy snapshot that causes it.
SINGLE_ANOMALY_CHANGES = {
    "metrics_missing": dict(metrics_changes=dict(missing=("connection trend over the last minute",))),
    "exporter_unreachable": dict(metrics_changes=dict(exporter_reaches_mysql=False)),
    "connections_climbing": dict(metrics_changes=dict(connections_change_per_second=0.5)),
    "normal_port_refused": dict(normal_error=1040),
    "connections_near_limit": dict(metrics_changes=dict(connections_now=55.0,
                                                        connections_last_known=55.0)),
}


def snapshot_with(anomaly_names):
    metrics_changes = {}
    normal_error = None
    for name in anomaly_names:
        change = SINGLE_ANOMALY_CHANGES[name]
        metrics_changes.update(change.get("metrics_changes", {}))
        normal_error = change.get("normal_error", normal_error)
    return snapshot(metrics(**metrics_changes), normal_error=normal_error)


MISSING_DATA_SHAPES = {
    "mysql_up missing": metrics(exporter_reaches_mysql=None, missing=("mysql_up",)),
    "connection count missing": metrics(
        connections_now=None, missing=("mysql_global_status_threads_connected",)),
    "trend missing": metrics(connections_change_per_second=None,
                             missing=("connection trend over the last minute",)),
    "all missing": MetricsEvidence(
        exporter_reaches_mysql=None, connections_now=None, connections_last_known=None,
        max_connections=None, connections_change_per_second=None,
        missing=("mysql_up", "mysql_global_status_threads_connected",
                 "connection trend over the last minute"),
    ),
}


def every_tested_snapshot():
    snapshots = [snapshot()]
    for size in range(1, len(SINGLE_ANOMALY_CHANGES) + 1):
        for names in itertools.combinations(SINGLE_ANOMALY_CHANGES, size):
            snapshots.append(snapshot_with(names))
    snapshots.extend(snapshot(shape) for shape in MISSING_DATA_SHAPES.values())
    snapshots.append(snapshot(MISSING_DATA_SHAPES["all missing"], normal_error=2003, path="none",
                              sessions=None))
    return snapshots


def test_healthy_snapshot_has_no_anomaly_and_says_so():
    finding = investigate_metrics(snapshot())

    assert isinstance(finding, AgentFinding)
    assert finding.agent == "metrics"
    assert finding.details["anomaly_types"] == []
    assert "nothing abnormal" in finding.rationale.lower()
    assert finding.weak_signals == ()


@pytest.mark.parametrize("evidence", every_tested_snapshot())
def test_never_names_a_cause_and_reuses_the_frozen_weak_signals(evidence):
    finding = investigate_metrics(evidence)

    assert finding.candidate is None
    assert finding.weak_signals == assess_metrics(evidence).weak_signals
    assert finding.onset == evidence.onset


@pytest.mark.parametrize("anomaly_name", sorted(SINGLE_ANOMALY_CHANGES))
def test_each_anomaly_type_appears_alone_when_its_condition_holds(anomaly_name):
    finding = investigate_metrics(snapshot_with([anomaly_name]))

    assert finding.details["anomaly_types"] == [anomaly_name]
    assert "nothing abnormal" not in finding.rationale.lower()
    assert finding.facts, "an abnormal finding must state what was observed"


@pytest.mark.parametrize(
    "anomaly_names",
    [names for size in range(2, len(SINGLE_ANOMALY_CHANGES) + 1)
     for names in itertools.combinations(sorted(SINGLE_ANOMALY_CHANGES), size)],
)
def test_combinations_give_exactly_those_types_sorted_without_duplicates(anomaly_names):
    finding = investigate_metrics(snapshot_with(anomaly_names))
    anomaly_types = finding.details["anomaly_types"]

    assert anomaly_types == sorted(set(anomaly_names))
    assert len(anomaly_types) == len(set(anomaly_types))
    assert set(anomaly_types) <= set(ALL_ANOMALY_TYPES)
    assert "nothing abnormal" not in finding.rationale.lower()


def test_climbing_threshold_is_the_frozen_one():
    just_below = investigate_metrics(snapshot(metrics(connections_change_per_second=0.29)))
    at_threshold = investigate_metrics(snapshot(metrics(connections_change_per_second=0.3)))

    assert "connections_climbing" not in just_below.details["anomaly_types"]
    assert "connections_climbing" in at_threshold.details["anomaly_types"]


def test_near_limit_is_at_85_percent_of_max_connections():
    # 51 of 60 is exactly 85%; 50 is just under.
    under = investigate_metrics(snapshot(metrics(connections_now=50.0, connections_last_known=50.0)))
    at_share = investigate_metrics(snapshot(metrics(connections_now=51.0, connections_last_known=51.0)))

    assert "connections_near_limit" not in under.details["anomaly_types"]
    assert "connections_near_limit" in at_share.details["anomaly_types"]
    assert any("51" in fact and "60" in fact for fact in at_share.facts)


def test_near_limit_falls_back_to_last_known_count_when_current_is_missing():
    finding = investigate_metrics(snapshot(metrics(
        connections_now=None, connections_last_known=58.0,
        missing=("mysql_global_status_threads_connected",),
    )))

    assert finding.details["anomaly_types"] == ["connections_near_limit", "metrics_missing"]


def test_near_limit_uses_current_count_over_last_known():
    finding = investigate_metrics(snapshot(metrics(connections_now=5.0, connections_last_known=58.0)))

    assert "connections_near_limit" not in finding.details["anomaly_types"]


@pytest.mark.parametrize("metrics_evidence", [
    metrics(max_connections=None, connections_now=58.0, connections_last_known=58.0),
    metrics(connections_now=None, connections_last_known=None),
])
def test_near_limit_is_skipped_when_a_count_is_unknown(metrics_evidence):
    finding = investigate_metrics(snapshot(metrics_evidence))

    assert "connections_near_limit" not in finding.details["anomaly_types"]


def test_exporter_state_unknown_counts_as_unreachable():
    # None is not True: an exporter that cannot be read is not "reaching MySQL".
    finding = investigate_metrics(snapshot(metrics(exporter_reaches_mysql=None)))

    assert finding.details["anomaly_types"] == ["exporter_unreachable"]
    assert any("exporter" in fact for fact in finding.facts)


@pytest.mark.parametrize("shape_name", sorted(MISSING_DATA_SHAPES))
def test_missing_metrics_are_a_sign_of_trouble_never_of_health(shape_name):
    metrics_evidence = MISSING_DATA_SHAPES[shape_name]
    finding = investigate_metrics(snapshot(metrics_evidence))
    rationale = finding.rationale.lower()

    assert "metrics_missing" in finding.details["anomaly_types"]
    assert "nothing abnormal" not in rationale
    assert "missing" in rationale and "sign of trouble" in rationale
    # Everything the collector listed, plus anything this agent found missing.
    assert finding.details["missing_metrics"][: len(metrics_evidence.missing)] == list(
        metrics_evidence.missing
    )
    for missing_name in metrics_evidence.missing:
        assert any(missing_name in fact for fact in finding.facts)


def test_all_metrics_missing_and_no_connection_is_still_not_healthy():
    finding = investigate_metrics(snapshot(MISSING_DATA_SHAPES["all missing"], normal_error=2003,
                                           path="none", sessions=None))

    assert finding.details["anomaly_types"] == [
        "exporter_unreachable", "metrics_missing", "normal_port_refused",
    ]
    assert finding.candidate is None
    assert "sign of trouble" in finding.rationale.lower()


def test_details_copy_the_evidence_values():
    metrics_evidence = metrics(connections_now=None, connections_last_known=42.0,
                               max_connections=60.0, connections_change_per_second=None,
                               missing=("mysql_global_status_threads_connected",
                                        "connection trend over the last minute"))
    finding = investigate_metrics(snapshot(metrics_evidence))

    assert finding.details["connections_now"] is None
    assert finding.details["connections_last_known"] == 42.0
    assert finding.details["max_connections"] == 60.0
    assert finding.details["connections_change_per_second"] is None
    assert finding.details["missing_metrics"] == [
        "mysql_global_status_threads_connected", "connection trend over the last minute",
    ]
    assert isinstance(finding.details["missing_metrics"], list)


def test_onset_is_copied_even_when_absent():
    assert investigate_metrics(snapshot(onset=None)).onset is None


def test_refused_port_fact_names_the_error_code():
    finding = investigate_metrics(snapshot(normal_error=1040))

    assert any("1040" in fact for fact in finding.facts)



@pytest.mark.parametrize(
    "changes, missing_name",
    [({"max_connections": None}, "max_connections"),
     ({"max_connections": 0.0}, "max_connections"),
     ({"connections_now": None, "connections_last_known": None}, "connection count")],
)
def test_values_needed_for_the_limit_are_never_read_as_health(changes, missing_name):
    from diagnosis.evidence import Evidence, MetricsEvidence

    metrics = MetricsEvidence(**{**dict(exporter_reaches_mysql=True, connections_now=5.0,
                                        connections_last_known=5.0, max_connections=60.0,
                                        connections_change_per_second=0.0, missing=()),
                                 **changes})

    finding = investigate_metrics(Evidence("normal", None, False, None, metrics, None, (), None))

    assert "metrics_missing" in finding.details["anomaly_types"]
    assert "connections_near_limit" not in finding.details["anomaly_types"]
    assert "nothing abnormal" not in finding.rationale.lower()
    assert "well under the limit" not in finding.rationale
    assert missing_name in finding.rationale and "sign of trouble" in finding.rationale
    assert any(missing_name in name for name in finding.details["missing_metrics"])


def test_an_unreachable_server_is_not_described_as_full():
    from diagnosis.evidence import Evidence, MetricsEvidence

    metrics = MetricsEvidence(True, 5.0, 5.0, 60.0, 0.0, ())

    unreachable = investigate_metrics(Evidence("none", 2003, False, None, metrics, None, (), None))
    full = investigate_metrics(Evidence("none", 1040, False, None, metrics, None, (), None))

    assert "full" not in unreachable.rationale and "2003" in unreachable.rationale
    assert "because the server is full" in full.rationale
