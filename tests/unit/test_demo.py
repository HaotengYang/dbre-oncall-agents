"""The demo's flow, with the lab replaced by stand-ins."""

import contextlib
import importlib.util
import pathlib

import pytest

from diagnosis.contract import Diagnosis, IndexSuggestion

DEMO_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "demo.py"


@pytest.fixture
def demo(monkeypatch):
    spec = importlib.util.spec_from_file_location("demo_under_test", DEMO_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events = []

    @contextlib.contextmanager
    def fake_injected_fault(fault_id):
        events.append(f"inject {fault_id}")
        yield {}
        events.append("fault stopped")

    monkeypatch.setattr(module, "wait_for_lab_baseline", lambda: events.append("baseline"))
    monkeypatch.setattr(module, "injected_fault", fake_injected_fault)
    monkeypatch.setattr(module, "load_diagnosis_access", lambda admin_port_available: None)
    monkeypatch.setattr(module, "render_report_text", lambda report: "REPORT")
    monkeypatch.setattr(module, "load_gate_access", lambda: None)
    monkeypatch.setattr(module, "format_plan", lambda plan: "PLAN")
    monkeypatch.setattr(module, "clear_lab", lambda: events.append("clear") or [])
    module.events = events
    return module


def _report_with(suggestion):
    class _Report:
        diagnosis = Diagnosis(root_cause="missing_index_slow_query", rationale="why",
                              index_suggestion=suggestion)
    return _Report()


def test_the_demo_injects_diagnoses_stops_rehearses_the_suggestion_and_clears(demo, monkeypatch):
    planned = []
    monkeypatch.setattr(demo, "orchestrate",
                        lambda alert, access: demo.events.append("diagnose")
                        or _report_with(IndexSuggestion("order_items", ("product_id",))))
    monkeypatch.setattr(demo, "make_plan", lambda access, request, query:
                        planned.append((request.table, request.columns, query)) or "plan")

    assert demo.run_demo(sleep=lambda seconds: None) == 0

    assert demo.events == ["clear", "baseline", f"inject {demo.DEMO_FAULT_ID}", "diagnose",
                           "fault stopped", "baseline", "clear"]
    assert planned == [("order_items", ("product_id",), demo.TARGET_QUERY)]


def test_without_a_suggestion_nothing_is_rehearsed(demo, monkeypatch):
    monkeypatch.setattr(demo, "orchestrate", lambda alert, access: _report_with(None))
    monkeypatch.setattr(demo, "make_plan", lambda *arguments: pytest.fail("no rehearsal"))
    assert demo.run_demo(sleep=lambda seconds: None) == 0
    assert demo.events[-1] == "clear"


def test_the_lab_is_cleared_even_when_the_diagnosis_fails(demo, monkeypatch):
    def broken(alert, access):
        raise RuntimeError("lost connection")
    monkeypatch.setattr(demo, "orchestrate", broken)
    with pytest.raises(RuntimeError):
        demo.run_demo(sleep=lambda seconds: None)
    assert demo.events[-1] == "clear"
