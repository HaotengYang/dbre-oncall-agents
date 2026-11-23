"""A failed evidence read becomes 'undetermined, escalate', never a crash."""

import pymysql
import pytest

from diagnosis import evidence as evidence_module
from diagnosis.contract import UNDETERMINED
from diagnosis.evidence import EvidenceCollectionFailed, describe_collection_error
from diagnosis.orchestrator import orchestrate, undetermined_after_failed_collection
from eval_runner import model_comparison

TIMEOUT = pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query (timed out)")


def test_the_error_description_names_type_code_and_message():
    assert describe_collection_error(TIMEOUT) == (
        "OperationalError 2013: Lost connection to MySQL server during query (timed out)")
    assert describe_collection_error(ConnectionResetError("reset")) == "ConnectionResetError: reset"


class _Cursor:
    def __init__(self, fail_on):
        self.fail_on = fail_on

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def execute(self, sql):
        if self.fail_on in sql:
            raise TIMEOUT
        self.last_sql = sql

    def fetchone(self):
        return (60,)

    def fetchall(self):
        return []


class _Connection:
    def __init__(self, fail_on):
        self.fail_on = fail_on
        self.closed = False

    def cursor(self):
        return _Cursor(self.fail_on)

    def close(self):
        self.closed = True


class _Access:
    prometheus_url = "http://prometheus"
    mysql_admin_port = None
    mysql_port = 13306
    mysql_host = "127.0.0.1"
    mysql_user = "shop_diag"
    mysql_password = "unused"


@pytest.mark.parametrize("fail_on, expected_step", [
    ("@@global.max_connections", "reading server settings"),
    ("events_statements_summary_by_digest", "reading statement digests at the start of the window"),
    ("performance_schema.processlist", "sampling sessions and lock waits"),
])
def test_collection_names_the_step_that_failed_and_closes_the_connection(
    monkeypatch, fail_on, expected_step
):
    connection = _Connection(fail_on)
    monkeypatch.setattr(evidence_module, "collect_metrics", lambda url: None)
    monkeypatch.setattr(evidence_module, "estimate_onset", lambda url: None)
    monkeypatch.setattr(evidence_module, "open_diagnosis_connection",
                        lambda access: (connection, "normal", None, False, None))
    monkeypatch.setattr(evidence_module.time, "sleep", lambda seconds: None)
    with pytest.raises(EvidenceCollectionFailed) as raised:
        evidence_module.collect_evidence(_Access())
    assert raised.value.step == expected_step
    assert "OperationalError 2013" in raised.value.reason
    assert connection.closed


def test_the_orchestrator_answers_undetermined_names_the_failed_read_and_escalates():
    def failing_collect(access):
        raise EvidenceCollectionFailed("sampling sessions and lock waits", TIMEOUT)

    report = orchestrate("Everything is slow.", access=None, collect=failing_collect,
                         sleep=lambda seconds: None)
    assert report.diagnosis.root_cause == UNDETERMINED
    assert len(report.rounds) == 3
    rationale = report.diagnosis.rationale
    assert "sampling sessions and lock waits failed" in rationale
    assert "OperationalError 2013" in rationale and "timed out" in rationale
    assert "escalate to a human" in rationale
    assert "sampling sessions and lock waits" in report.diagnosis.evidence[0]


def test_an_unexpected_error_is_still_undetermined_with_its_type():
    verdict = undetermined_after_failed_collection(RuntimeError("boom"))
    assert verdict.root_cause == UNDETERMINED
    assert "RuntimeError: boom" in verdict.rationale and "escalate to a human" in verdict.rationale


def test_the_recording_run_scores_a_failed_collection_as_undetermined_and_records_nothing(
    monkeypatch, tmp_path
):
    answers = []

    def fake_run_case(case, diagnose):
        answers.append(diagnose(case.alert, None))
        return model_comparison.CaseResult.__new__(model_comparison.CaseResult)

    def failing_collect(access):
        raise EvidenceCollectionFailed("sampling sessions and lock waits", TIMEOUT)

    class _Case:
        case_id = "incident-example"
        alert = "Everything is slow."

    monkeypatch.setattr(model_comparison, "run_case", fake_run_case)
    monkeypatch.setattr(model_comparison, "collect_evidence", failing_collect)
    monkeypatch.setattr(model_comparison.CaseResult, "outcome", "acceptable", raising=False)
    model_comparison.record_cases([_Case()], recordings_directory=tmp_path)
    assert answers[0].root_cause == UNDETERMINED
    assert "escalate to a human" in answers[0].rationale
    assert list(tmp_path.iterdir()) == []
