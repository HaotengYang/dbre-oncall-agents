"""The metrics agent: reads the Prometheus side of a snapshot and says what
kind of anomaly it shows, and since when.

It reuses the frozen rule engine's assess_metrics and its thresholds; it does
not judge anything on its own terms. It never names a root cause (candidate is
always None): from metrics alone a climbing connection count could be a leak,
a lock pile-up or slow queries. The orchestrator combines this finding with
the SQL agent's.

A metric that has disappeared is evidence of an anomaly, never of health.

details keys:
  anomaly_types: sorted list, drawn from exactly these values:
    "metrics_missing"         some expected metric is absent
    "exporter_unreachable"    the exporter is not known to reach MySQL
    "connections_climbing"    the connection count is rising (frozen threshold)
    "normal_port_refused"     the normal port refused a connection
    "connections_near_limit"  the current connection count, or the last known
                              one if the current one is missing, is at least
                              NEARLY_FULL_SHARE of max_connections
    Empty when nothing is abnormal.
  connections_now, connections_last_known, max_connections,
  connections_change_per_second: copied from the evidence; None stays None.
  missing_metrics: list of the metrics that are absent.
"""

from diagnosis.agents.base import AgentFinding
from diagnosis.rules import NEARLY_FULL_SHARE, assess_metrics

AGENT_NAME = "metrics"


def _connection_count_for_limit(metrics_evidence):
    if metrics_evidence.connections_now is not None:
        return metrics_evidence.connections_now
    return metrics_evidence.connections_last_known


def investigate_metrics(evidence):
    """Judges one evidence snapshot's metrics. Returns an AgentFinding."""
    metrics_evidence = evidence.metrics
    assessment = assess_metrics(evidence)
    anomaly_types = set()
    facts = []
    problems = []

    if metrics_evidence.missing:
        anomaly_types.add("metrics_missing")
        facts.append("metrics missing: " + ", ".join(metrics_evidence.missing))
    if metrics_evidence.exporter_reaches_mysql is not True:
        anomaly_types.add("exporter_unreachable")
        if metrics_evidence.exporter_reaches_mysql is False:
            facts.append("exporter cannot reach MySQL")
            problems.append("the exporter cannot reach MySQL")
        else:
            facts.append("exporter state unknown: it could not be read")
            problems.append("whether the exporter reaches MySQL cannot be read")
    if assessment.connections_climbing:
        anomaly_types.add("connections_climbing")
        facts.append(
            f"connection count climbing at {assessment.connections_change_per_second:.1f} a second"
        )
        problems.append("the connection count is climbing")
    if evidence.normal_port_error_code is not None:
        anomaly_types.add("normal_port_refused")
        facts.append(f"normal port refused a connection (error {evidence.normal_port_error_code})")
        problems.append("the normal port refused a connection")

    connection_count = _connection_count_for_limit(metrics_evidence)
    max_connections = metrics_evidence.max_connections
    if connection_count is not None and max_connections is not None:
        count_label = (
            "connection count" if metrics_evidence.connections_now is not None
            else "last known connection count"
        )
        facts.append(f"{count_label} {connection_count:.0f} of {max_connections:.0f}")
        if connection_count >= NEARLY_FULL_SHARE * max_connections:
            anomaly_types.add("connections_near_limit")
            problems.append("connections are near the limit")

    if not anomaly_types:
        rationale = (
            "Metrics show nothing abnormal: all expected metrics are present, the exporter "
            "reaches MySQL, and connections are well under the limit and not climbing."
        )
    else:
        sentences = []
        if problems:
            sentences.append("Metrics show that " + "; ".join(problems) + ".")
        if metrics_evidence.missing:
            sentences.append(
                "Some metrics are missing (" + ", ".join(metrics_evidence.missing) + "), and "
                "their absence is itself a sign of trouble, not of health."
            )
        rationale = " ".join(sentences)

    return AgentFinding(
        agent=AGENT_NAME,
        candidate=None,
        symptoms=(),
        rationale=rationale,
        facts=tuple(facts),
        weak_signals=assessment.weak_signals,
        details={
            "anomaly_types": sorted(anomaly_types),
            "connections_now": metrics_evidence.connections_now,
            "connections_last_known": metrics_evidence.connections_last_known,
            "max_connections": metrics_evidence.max_connections,
            "connections_change_per_second": metrics_evidence.connections_change_per_second,
            "missing_metrics": list(metrics_evidence.missing),
        },
        onset=evidence.onset,
    )
