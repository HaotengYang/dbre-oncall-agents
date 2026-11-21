"""The agent contract itself."""

import dataclasses

import pytest

from diagnosis.agents.base import AgentFinding


def test_finding_is_immutable_and_has_empty_details_by_default():
    finding = AgentFinding(agent="sql", candidate=None, symptoms=(), rationale="",
                           facts=(), weak_signals=())

    assert finding.details == {} and finding.index_suggestion is None and finding.onset is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.candidate = "lock_wait"
