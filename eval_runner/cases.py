"""Loads and validates eval case files (JSON, one case per file)."""

import json
import pathlib
from dataclasses import dataclass
from typing import Optional

FAULT_LABELS = ("missing_index_slow_query", "lock_wait", "connection_exhaustion")
ANSWER_LABELS = FAULT_LABELS + ("healthy", "undetermined")
TIERS = ("basic", "hard", "holdout")
KINDS = (
    "single_fault", "healthy", "healthy_lookalike", "cascade",
    "incomplete_evidence", "hostile_input", "metrics_gap",
)
TOP_LEVEL_FIELDS = (
    "id", "tier", "kind", "scenario", "observe_seconds", "admin_port_available",
    "alert", "expected",
)
REQUIRED_EXPECTED_FIELDS = ("root_cause", "also_acceptable", "symptoms")
OPTIONAL_EXPECTED_FIELDS = ("index",)


class CaseError(Exception):
    """A case file is malformed. The message names the file and the field."""


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    tier: str
    kind: str
    scenario: Optional[str]
    observe_seconds: int
    admin_port_available: bool
    alert: str
    expected_root_cause: str
    also_acceptable: tuple
    expected_symptoms: tuple
    expected_index: Optional[tuple]  # (table, (column, ...)) or None


def _check_fields(content, required, optional, where, case_path):
    if not isinstance(content, dict):
        raise CaseError(f"{case_path.name}: {where} must be an object")
    for field_name in required:
        if field_name not in content:
            raise CaseError(f"{case_path.name}: {where} is missing the field '{field_name}'")
    for field_name in content:
        if field_name not in required and field_name not in optional:
            raise CaseError(f"{case_path.name}: {where} has an unknown field '{field_name}'")


def _labels(value, allowed, field_name, case_path):
    if not isinstance(value, list) or any(label not in allowed for label in value):
        raise CaseError(
            f"{case_path.name}: '{field_name}' must be a list of labels from {list(allowed)}, "
            f"got {value!r}"
        )
    return tuple(value)


def load_case(case_path):
    case_path = pathlib.Path(case_path)
    try:
        content = json.loads(case_path.read_text())
    except ValueError as parse_error:
        raise CaseError(f"{case_path.name}: not valid JSON ({parse_error})") from None

    _check_fields(content, TOP_LEVEL_FIELDS, (), "the case", case_path)

    def fail(field_name, problem):
        raise CaseError(f"{case_path.name}: '{field_name}' {problem}, got {content[field_name]!r}")

    if not isinstance(content["id"], str) or not content["id"]:
        fail("id", "must be a non-empty string")
    if content["tier"] not in TIERS:
        fail("tier", f"must be one of {list(TIERS)}")
    if content["kind"] not in KINDS:
        fail("kind", f"must be one of {list(KINDS)}")
    observe_seconds = content["observe_seconds"]
    if isinstance(observe_seconds, bool) or not isinstance(observe_seconds, int) or observe_seconds <= 0:
        fail("observe_seconds", "must be a positive whole number")
    if not isinstance(content["admin_port_available"], bool):
        fail("admin_port_available", "must be true or false")
    if not isinstance(content["alert"], str) or not content["alert"].strip():
        fail("alert", "must be a non-empty string")

    scenario = content["scenario"]
    if content["kind"] == "healthy":
        if scenario is not None:
            fail("scenario", "must be null for kind 'healthy' (nothing is injected)")
    elif not isinstance(scenario, str) or not scenario:
        fail("scenario", f"must name an injector scenario for kind '{content['kind']}'")

    expected = content["expected"]
    _check_fields(expected, REQUIRED_EXPECTED_FIELDS, OPTIONAL_EXPECTED_FIELDS,
                  "'expected'", case_path)
    if expected["root_cause"] not in FAULT_LABELS + ("healthy",):
        raise CaseError(
            f"{case_path.name}: 'expected.root_cause' must be a fault label or 'healthy', "
            f"got {expected['root_cause']!r}"
        )
    # Only "undetermined" can be acceptable-but-not-a-hit. Allowing a fault
    # label here would let a case turn a symptom or a false positive into an
    # acceptable answer.
    also_acceptable = _labels(expected["also_acceptable"], ("undetermined",),
                              "expected.also_acceptable", case_path)
    if also_acceptable and expected["root_cause"] == "healthy":
        raise CaseError(
            f"{case_path.name}: 'expected.also_acceptable' must be empty when the expected "
            "answer is 'healthy'; 'undetermined' there is a mild false alarm"
        )
    symptoms = _labels(expected["symptoms"], FAULT_LABELS, "expected.symptoms", case_path)
    if symptoms and content["kind"] != "cascade":
        raise CaseError(f"{case_path.name}: 'expected.symptoms' is only allowed on cascade cases")

    expected_index = None
    if "index" in expected:
        index = expected["index"]
        index_is_valid = (
            isinstance(index, dict) and set(index) == {"table", "columns"}
            and isinstance(index["table"], str) and index["table"]
            and isinstance(index["columns"], list) and index["columns"]
            and all(isinstance(column, str) and column for column in index["columns"])
        )
        if not index_is_valid:
            raise CaseError(
                f"{case_path.name}: 'expected.index' must be "
                f'{{"table": "...", "columns": ["...", ...]}}, got {index!r}'
            )
        expected_index = (index["table"], tuple(index["columns"]))

    return EvalCase(
        case_id=content["id"], tier=content["tier"], kind=content["kind"], scenario=scenario,
        observe_seconds=observe_seconds, admin_port_available=content["admin_port_available"],
        alert=content["alert"], expected_root_cause=expected["root_cause"],
        also_acceptable=also_acceptable, expected_symptoms=symptoms,
        expected_index=expected_index,
    )


def load_cases(cases_directory):
    """Loads every *.json case in a directory, sorted by case id."""
    cases = [load_case(case_path) for case_path in sorted(pathlib.Path(cases_directory).glob("*.json"))]
    seen_ids = set()
    for case in cases:
        if case.case_id in seen_ids:
            raise CaseError(f"case id '{case.case_id}' is used by more than one file")
        seen_ids.add(case.case_id)
    return sorted(cases, key=lambda case: case.case_id)
