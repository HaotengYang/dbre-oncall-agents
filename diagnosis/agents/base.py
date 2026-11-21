"""The contract every diagnosis agent follows.

An agent receives one Evidence snapshot and returns one AgentFinding. It does
not open connections, collect evidence or call other agents: collection is the
evidence module's job and merging is the orchestrator's. That keeps each agent
a pure function of its input, testable without a database.
"""

from dataclasses import dataclass, field
from typing import Optional

from diagnosis.contract import IndexSuggestion


@dataclass(frozen=True)
class AgentFinding:
    agent: str  # "sql" or "metrics"
    # A root cause this agent can name on its own evidence, or None.
    candidate: Optional[str]
    # Anomalies this agent sees that it judges to be consequences.
    symptoms: tuple
    # Plain language: what the agent concluded and why. May name tables and
    # columns taken from captured SQL. Can be empty when nothing is named.
    rationale: str
    # Observations the conclusion rests on. May quote captured SQL.
    # rationale, facts, details and index_suggestion are all untrusted as far
    # as a model prompt is concerned: fence them with diagnosis.untrusted.
    facts: tuple
    # Things that look off but do not name a cause.
    weak_signals: tuple
    # Agent-specific detail the orchestrator may use. Keys are documented by
    # each agent.
    details: dict = field(default_factory=dict)
    index_suggestion: Optional[IndexSuggestion] = None
    onset: Optional[str] = None
