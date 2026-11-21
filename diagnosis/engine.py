"""The diagnosis entry point used by the eval runner and, later, the agents."""

from diagnosis.evidence import collect_evidence
from diagnosis.rules import decide


def diagnose_with_rules(alert_text, access):
    """Diagnoses with the rule engine only.

    alert_text is accepted because every backend has the same signature. The
    rule engine does not read it: it judges from evidence, not from how the
    alert happens to be worded.
    """
    return decide(collect_evidence(access))
