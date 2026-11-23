"""The diagnosis entry point used by the eval runner and, later, the agents."""

from diagnosis.evidence import EvidenceCollectionFailed, collect_evidence
from diagnosis.orchestrator import undetermined_after_failed_collection
from diagnosis.rules import decide


def diagnose_with_rules(alert_text, access):
    """Diagnoses with the rule engine only.

    alert_text is accepted because every backend has the same signature. The
    rule engine does not read it: it judges from evidence, not from how the
    alert happens to be worded.
    """
    try:
        evidence = collect_evidence(access)
    except EvidenceCollectionFailed as collection_error:
        return undetermined_after_failed_collection(collection_error)
    return decide(evidence)
