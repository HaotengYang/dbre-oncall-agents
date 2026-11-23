"""Evidence to and from plain JSON, for recording and replay.

A recording is evidence only: what the diagnosis side saw. It carries no case
id, scenario or expected answer; the eval runner keeps that mapping.
"""

import dataclasses

from diagnosis.evidence import (
    Evidence,
    MetricsEvidence,
    SessionEvidence,
    StatementLoad,
)
from diagnosis.explain import ExplainResult, PlanRow


def evidence_to_dict(evidence):
    return dataclasses.asdict(evidence)


def _explain_from_dict(content):
    if content is None:
        return None
    return ExplainResult(
        status=content["status"], reason=content["reason"],
        plan=tuple(PlanRow(**row) for row in content["plan"]),
    )


def evidence_from_dict(content):
    sessions = content["sessions"]
    return Evidence(
        connection_path=content["connection_path"],
        normal_port_error_code=content["normal_port_error_code"],
        admin_port_tried=content["admin_port_tried"],
        admin_port_error_code=content["admin_port_error_code"],
        metrics=MetricsEvidence(**{**content["metrics"],
                                   "missing": tuple(content["metrics"]["missing"])}),
        sessions=None if sessions is None else SessionEvidence(**{
            **sessions,
            "blocking_transactions": tuple(tuple(b) for b in sessions["blocking_transactions"]),
        }),
        statements=tuple(
            StatementLoad(**{**load, "explain": _explain_from_dict(load["explain"])})
            for load in content["statements"]
        ),
        onset=content["onset"],
    )
