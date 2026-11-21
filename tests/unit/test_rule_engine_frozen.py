"""The rule engine is frozen (CLAUDE.md, 2026-11-21).

It was tagged 'rule-engine-frozen-v1' when step C1 was complete. Changing it
needs the owner's explicit consent, a recorded reason, and an update of the
digest below in the same commit. It must never be changed to pass the holdout
cases.
"""

import hashlib
import pathlib

RULES_PATH = pathlib.Path(__file__).resolve().parents[2] / "diagnosis" / "rules.py"
FROZEN_SHA256 = "47648d97947a9bbbeec88f0d1d805aa05f5e2a3c3d645cb4ccf798cf31e1158c"


def test_rule_engine_is_unchanged_since_it_was_frozen():
    actual = hashlib.sha256(RULES_PATH.read_bytes()).hexdigest()

    assert actual == FROZEN_SHA256, (
        "diagnosis/rules.py has changed since it was frozen. This needs the owner's "
        "explicit consent; see CLAUDE.md."
    )
