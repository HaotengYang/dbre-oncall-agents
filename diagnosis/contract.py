"""The shape of a diagnosis (docs/DESIGN.md, section 3)."""

from dataclasses import dataclass, field
from typing import Optional

MISSING_INDEX = "missing_index_slow_query"
LOCK_WAIT = "lock_wait"
CONNECTION_EXHAUSTION = "connection_exhaustion"
HEALTHY = "healthy"
UNDETERMINED = "undetermined"
ROOT_CAUSES = (MISSING_INDEX, LOCK_WAIT, CONNECTION_EXHAUSTION, HEALTHY, UNDETERMINED)


@dataclass(frozen=True)
class IndexSuggestion:
    table: str
    columns: tuple


@dataclass(frozen=True)
class Diagnosis:
    root_cause: str
    rationale: str  # plain language: why this cause, why not the others
    symptoms: tuple = ()  # anomalies present that are consequences, not causes
    onset: Optional[str] = None  # ISO 8601 UTC, when the anomaly started
    evidence: tuple = ()  # the specific observations the conclusion rests on
    recommended_steps: tuple = ()  # filled by the SOP agent (step C5)
    index_suggestion: Optional[IndexSuggestion] = None
    rounds_used: int = 1

    def __post_init__(self):
        if self.root_cause not in ROOT_CAUSES:
            raise ValueError(f"unknown root cause {self.root_cause!r}")
