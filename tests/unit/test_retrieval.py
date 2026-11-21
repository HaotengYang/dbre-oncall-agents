"""Keyword runbook retrieval and the SOP agent."""

import json
import pathlib

import pytest

from diagnosis.contract import Diagnosis
from diagnosis.retrieval import (
    KeywordRetriever,
    Retriever,
    load_runbooks,
    parse_runbook,
    recommend_steps,
    tokenize,
)
from eval_runner.retrieval_eval import evaluate_retriever, summarize_retrieval

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_ten_runbooks_load_from_the_index_with_their_steps():
    runbooks = load_runbooks()

    assert len(runbooks) == 10
    assert len({runbook.runbook_id for runbook in runbooks}) == 10
    assert all(runbook.steps for runbook in runbooks)
    index_names = (REPOSITORY_ROOT / "runbooks" / "INDEX").read_text().split()
    assert sorted(index_names) == sorted(p.name for p in (REPOSITORY_ROOT / "runbooks").glob("*.md"))


def test_parse_reads_header_and_numbered_steps_only():
    runbook = parse_runbook("---\nid: X1\ntitle: T\ncauses: lock_wait, other\n---\n# T\n\n"
                            "Signs: s.\n\n1. First step.\n2. Second step.\nnot a step\n")

    assert (runbook.runbook_id, runbook.causes) == ("X1", ("lock_wait", "other"))
    assert runbook.steps == ("First step.", "Second step.")


def test_tokenize_lowercases_drops_stop_words_and_folds_suffixes():
    assert tokenize("The Locks were LOCKED, locking") == ["lock", "lock", "lock"]


def test_keyword_retriever_ranks_by_relevance_and_is_a_retriever():
    retriever = KeywordRetriever()

    hits = retriever.search("waiting for table metadata lock after ALTER", top_k=3)

    assert isinstance(retriever, Retriever)
    assert hits[0].runbook.runbook_id == "RB03"
    assert [h.score for h in hits] == sorted([h.score for h in hits], reverse=True)
    assert retriever.search("zzz qqq", top_k=3) == []


@pytest.mark.parametrize("root_cause, alert, expected_runbook", [
    ("lock_wait", "Order status updates are timing out.", "RB02"),
    ("missing_index_slow_query", "Customer order history pages have become slow.", "RB01"),
    ("connection_exhaustion", "New application instances cannot get a database connection.", "RB05"),
])
def test_sop_agent_quotes_the_matching_runbook(root_cause, alert, expected_runbook):
    steps = recommend_steps(Diagnosis(root_cause=root_cause, rationale="r"), alert)

    assert steps and all(step.startswith(f"[{expected_runbook} ") for step in steps)


@pytest.mark.parametrize("root_cause", ["healthy", "undetermined"])
def test_no_steps_for_healthy_or_undetermined(root_cause):
    assert recommend_steps(Diagnosis(root_cause=root_cause, rationale="r"), "anything") == ()


def test_retrieval_eval_counts_hits_and_separates_wording_mismatch():
    queries = json.loads((REPOSITORY_ROOT / "evals" / "retrieval" / "queries.json").read_text())

    rows = evaluate_retriever(KeywordRetriever(), queries)
    summary = summarize_retrieval(rows)

    assert len(rows) == 16
    assert summary["matching_hit_at_1"].denominator == 8
    assert summary["mismatch_hit_at_1"].denominator == 8
    # The measured baseline for keyword retrieval (2026-11-21).
    assert summary["matching_hit_at_1"].text() == "100.0% (8/8)"
    # After review, three mismatch queries were reworded because they shared
    # words with their runbook; 5/8 before, 3/8 after.
    assert summary["mismatch_hit_at_1"].text() == "37.5% (3/8)"


def test_orchestrator_report_carries_the_steps():
    from tests.unit.test_orchestrator import LOCK_WAIT, ScriptedCollector, orchestrate
    from diagnosis.orchestrator import render_report_text

    report = orchestrate("Order updates time out.", access=None, collect=ScriptedCollector(LOCK_WAIT))

    assert report.diagnosis.recommended_steps[0].startswith("[RB02 ")
    assert "Recommended steps (quoted from the team's runbook):" in render_report_text(report)


@pytest.mark.parametrize("root_cause, misleading_alert", [
    ("lock_wait", "Replica lag growing, Seconds_Behind_Source rising, replicas stale"),
    ("connection_exhaustion", "Disk space almost gone, no space left on device, binary logs growing"),
    ("missing_index_slow_query", "Deadlock found when trying to get lock, transaction rolled back"),
])
def test_steps_always_come_from_a_runbook_for_the_diagnosed_cause(root_cause, misleading_alert):
    steps = recommend_steps(Diagnosis(root_cause=root_cause, rationale="r"), misleading_alert)

    runbooks = {runbook.runbook_id: runbook for runbook in load_runbooks()}
    assert steps
    runbook_id = steps[0][1:].split(" ")[0]
    assert root_cause in runbooks[runbook_id].causes


def test_index_may_not_point_outside_the_runbooks(tmp_path):
    (tmp_path / "INDEX").write_text("../var/injections.jsonl\n")

    with pytest.raises(ValueError, match="plain .md file names"):
        load_runbooks(tmp_path)


def test_windows_line_endings_still_give_steps():
    runbook = parse_runbook("---\r\nid: X\r\ntitle: T\r\ncauses: lock_wait\r\n---\r\n1. Step.\r\n")

    assert runbook.steps == ("Step.",)


def test_a_retrieval_failure_does_not_lose_the_diagnosis(monkeypatch):
    from diagnosis import orchestrator
    from tests.unit.test_orchestrator import LOCK_WAIT, ScriptedCollector, orchestrate

    def broken(diagnosis, alert_text):
        raise FileNotFoundError("runbooks/INDEX")

    monkeypatch.setattr(orchestrator, "recommend_steps", broken)

    report = orchestrate("alert", access=None, collect=ScriptedCollector(LOCK_WAIT))

    assert report.diagnosis.root_cause == "lock_wait"
    assert "runbook retrieval failed" in report.diagnosis.recommended_steps[0]
