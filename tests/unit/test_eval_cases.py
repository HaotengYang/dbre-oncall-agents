"""Case files are the evals' ground truth. A malformed one must be rejected
when it is loaded, naming the file and the field, not silently mis-scored."""

import json

import pytest

from eval_runner.cases import CaseError, load_case, load_cases

VALID_CASE = {
    "id": "cascade-lock-wait",
    "tier": "hard",
    "kind": "cascade",
    "scenario": "cascade.lock_wait_exhausts_connections",
    "observe_seconds": 45,
    "admin_port_available": True,
    "alert": "Checkout cannot get a database connection.",
    "expected": {
        "root_cause": "lock_wait",
        "also_acceptable": [],
        "symptoms": ["connection_exhaustion"],
    },
}


def write_case(directory, content, file_name="case.json"):
    case_path = directory / file_name
    case_path.write_text(json.dumps(content))
    return case_path


def changed(**changes):
    return {**VALID_CASE, **changes}


def test_valid_case_loads_with_typed_fields(tmp_path):
    case = load_case(write_case(tmp_path, VALID_CASE))

    assert case.case_id == "cascade-lock-wait"
    assert case.tier == "hard" and case.kind == "cascade"
    assert case.scenario == "cascade.lock_wait_exhausts_connections"
    assert case.observe_seconds == 45 and case.admin_port_available is True
    assert case.expected_root_cause == "lock_wait"
    assert case.also_acceptable == () and case.expected_symptoms == ("connection_exhaustion",)
    assert case.expected_index is None


def test_expected_index_is_loaded_for_missing_index_cases(tmp_path):
    content = changed(
        kind="single_fault", scenario="missing_index.dropped_index",
        expected={"root_cause": "missing_index_slow_query", "also_acceptable": [],
                  "symptoms": [], "index": {"table": "orders",
                                            "columns": ["customer_id", "created_at"]}},
    )

    case = load_case(write_case(tmp_path, content))

    assert case.expected_index == ("orders", ("customer_id", "created_at"))


@pytest.mark.parametrize("missing_field", sorted(VALID_CASE))
def test_missing_top_level_field_is_rejected_by_name(tmp_path, missing_field):
    content = dict(VALID_CASE)
    del content[missing_field]

    with pytest.raises(CaseError) as rejected:
        load_case(write_case(tmp_path, content))

    assert missing_field in str(rejected.value) and "case.json" in str(rejected.value)


@pytest.mark.parametrize(
    "content, named_in_error",
    [
        (changed(surprise=1), "surprise"),
        (changed(tier="medium"), "tier"),
        (changed(kind="mystery"), "kind"),
        (changed(observe_seconds=0), "observe_seconds"),
        (changed(observe_seconds="long"), "observe_seconds"),
        (changed(admin_port_available="yes"), "admin_port_available"),
        (changed(alert=""), "alert"),
        (changed(expected={"root_cause": "gremlins", "also_acceptable": [], "symptoms": []}),
         "root_cause"),
        (changed(expected={"root_cause": "lock_wait", "also_acceptable": ["gremlins"],
                           "symptoms": []}), "also_acceptable"),
        # A symptom, the root cause itself, or "healthy" must not be acceptable.
        (changed(expected={"root_cause": "lock_wait",
                           "also_acceptable": ["connection_exhaustion"],
                           "symptoms": ["connection_exhaustion"]}), "also_acceptable"),
        (changed(expected={"root_cause": "lock_wait", "also_acceptable": ["healthy"],
                           "symptoms": []}), "also_acceptable"),
        (changed(kind="healthy", scenario=None,
                 expected={"root_cause": "healthy", "also_acceptable": ["undetermined"],
                           "symptoms": []}), "also_acceptable"),
        (changed(expected={"root_cause": "lock_wait", "also_acceptable": [],
                           "symptoms": ["healthy"]}), "symptoms"),
        (changed(expected={"root_cause": "lock_wait", "also_acceptable": []}), "symptoms"),
        (changed(expected={"root_cause": "lock_wait", "also_acceptable": [], "symptoms": [],
                           "extra": 1}), "extra"),
        (changed(expected={"root_cause": "lock_wait", "also_acceptable": [], "symptoms": [],
                           "index": {"table": "orders"}}), "index"),
    ],
)
def test_invalid_content_is_rejected_by_name(tmp_path, content, named_in_error):
    with pytest.raises(CaseError) as rejected:
        load_case(write_case(tmp_path, content))

    assert named_in_error in str(rejected.value)


def test_healthy_case_must_not_name_a_fault_scenario_expecting_healthy(tmp_path):
    # kind "healthy" means nothing is injected at all.
    content = changed(kind="healthy", scenario="lock_wait.long_transaction",
                      expected={"root_cause": "healthy", "also_acceptable": [], "symptoms": []})

    with pytest.raises(CaseError, match="scenario"):
        load_case(write_case(tmp_path, content))


def test_non_healthy_kind_requires_a_scenario(tmp_path):
    with pytest.raises(CaseError, match="scenario"):
        load_case(write_case(tmp_path, changed(scenario=None)))


def test_symptoms_are_only_allowed_on_cascades(tmp_path):
    content = changed(kind="single_fault")

    with pytest.raises(CaseError, match="symptoms"):
        load_case(write_case(tmp_path, content))


def test_file_that_is_not_json_is_rejected_with_its_name(tmp_path):
    broken_path = tmp_path / "broken.json"
    broken_path.write_text("{not json")

    with pytest.raises(CaseError, match="broken.json"):
        load_case(broken_path)


def test_load_cases_reads_every_file_sorted_and_rejects_duplicate_ids(tmp_path):
    write_case(tmp_path, changed(id="b-case"), "2.json")
    write_case(tmp_path, changed(id="a-case"), "1.json")

    assert [case.case_id for case in load_cases(tmp_path)] == ["a-case", "b-case"]

    write_case(tmp_path, changed(id="a-case"), "3.json")
    with pytest.raises(CaseError, match="a-case"):
        load_cases(tmp_path)
