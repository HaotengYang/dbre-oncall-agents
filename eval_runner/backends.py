"""The diagnosis functions the runner can score.

All share one signature: diagnose(alert_text, access) -> Diagnosis. The two
"always" backends are deliberately wrong. Running them proves that the scoring
is wired to reality: a system that always says "healthy" must score no hits.
"""

from diagnosis.contract import CONNECTION_EXHAUSTION, HEALTHY, Diagnosis
from diagnosis.engine import diagnose_with_rules
from diagnosis.orchestrator import diagnose_with_orchestrator


def always_healthy(alert_text, access):
    return Diagnosis(root_cause=HEALTHY, rationale="Test double: always answers healthy.")


def always_connection_exhaustion(alert_text, access):
    return Diagnosis(
        root_cause=CONNECTION_EXHAUSTION,
        rationale="Test double: always answers connection exhaustion.",
    )


BACKENDS = {
    "rule_engine": diagnose_with_rules,
    # The agents and up to three rounds. Same frozen rules, more looks.
    "orchestrator": diagnose_with_orchestrator,
    "always_healthy": always_healthy,
    "always_connection_exhaustion": always_connection_exhaustion,
}
