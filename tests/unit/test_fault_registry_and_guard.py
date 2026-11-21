"""Fault ids and labels are stable names the evals depend on; the lab guard
must refuse anything that does not identify itself as the lab."""

import re

import pymysql
import pytest

from fault_injector.lab import NotLabEnvironmentError, assert_lab_environment
from fault_injector.registry import ROOT_CAUSE_LABELS, find_fault, list_faults


def test_fault_ids_have_the_form_class_dot_variant_and_are_unique():
    fault_ids = [fault.fault_id for fault in list_faults()]

    assert fault_ids
    assert len(fault_ids) == len(set(fault_ids))
    for fault_id in fault_ids:
        assert re.fullmatch(r"[a-z_]+\.[a-z_]+", fault_id), fault_id


def test_every_fault_uses_a_root_cause_label_from_the_design():
    assert ROOT_CAUSE_LABELS == {
        "missing_index_slow_query", "lock_wait", "connection_exhaustion",
    }
    for fault in list_faults():
        assert fault.root_cause in ROOT_CAUSE_LABELS, fault.fault_id


def test_expected_symptoms_are_labels_other_than_the_root_cause():
    for fault in list_faults():
        assert set(fault.expected_symptoms) <= ROOT_CAUSE_LABELS, fault.fault_id
        assert fault.root_cause not in fault.expected_symptoms, fault.fault_id


def test_cascading_faults_name_connection_exhaustion_as_a_symptom_not_the_cause():
    cascades = [fault for fault in list_faults() if fault.fault_id.startswith("cascade.")]

    assert len(cascades) == 2
    assert {fault.root_cause for fault in cascades} == {"lock_wait", "missing_index_slow_query"}
    for fault in cascades:
        assert fault.expected_symptoms == ("connection_exhaustion",)


def test_each_class_has_two_or_three_variants():
    variants_per_class = {}
    for fault in list_faults():
        fault_class = fault.fault_id.split(".")[0]
        variants_per_class[fault_class] = variants_per_class.get(fault_class, 0) + 1

    assert variants_per_class == {
        "missing_index": 2, "lock_wait": 2, "connection_exhaustion": 2, "cascade": 2,
    }


def test_every_fault_describes_what_it_changes():
    for fault in list_faults():
        assert fault.planned_changes and all(fault.planned_changes), fault.fault_id
        assert fault.summary, fault.fault_id


def test_unknown_fault_id_is_rejected_with_the_available_ids():
    with pytest.raises(KeyError, match="missing_index.dropped_index"):
        find_fault("no_such.fault")


class FakeCursor:
    def __init__(self, rows=None, error=None):
        self._rows, self._error = rows, error

    def __enter__(self):
        return self

    def __exit__(self, *exception_details):
        return False

    def execute(self, sql):
        if self._error:
            raise self._error

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows=None, error=None):
        self._rows, self._error = rows, error

    def cursor(self):
        return FakeCursor(self._rows, self._error)


def test_guard_accepts_the_lab_marker():
    assert_lab_environment(FakeConnection(rows=(("dbre-lab",),)))


@pytest.mark.parametrize(
    "rows",
    [(), (("production",),), (("dbre-lab",), ("something-else",))],
)
def test_guard_refuses_a_missing_or_different_marker(rows):
    with pytest.raises(NotLabEnvironmentError):
        assert_lab_environment(FakeConnection(rows=rows))


def test_guard_refuses_when_the_marker_table_does_not_exist():
    missing_table = pymysql.err.ProgrammingError(1146, "Table 'lab_meta.environment' doesn't exist")

    with pytest.raises(NotLabEnvironmentError):
        assert_lab_environment(FakeConnection(error=missing_table))
