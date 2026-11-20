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
