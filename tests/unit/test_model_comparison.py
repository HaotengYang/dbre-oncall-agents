"""Replay and spending, with a fake client and recordings in a temp folder."""

import json

import pytest

from diagnosis.serialization import evidence_to_dict
from eval_runner.cases import load_cases
from eval_runner.cli import EVALS_DIRECTORY
from eval_runner.model_comparison import (
    BudgetExhausted,
    SpendLedger,
    format_replay_report,
    replay,
    stability,
)
from tests.unit.test_llm_backend import EVIDENCE, GOOD_ANSWER, FakeClient

CASES = {case.case_id: case for case in load_cases(EVALS_DIRECTORY / "cases")}
LOCK_CASE = CASES["basic-long-transaction"]
HEALTHY_CASE = CASES["basic-healthy-quiet"]


@pytest.fixture
def recordings(tmp_path):
    directory = tmp_path / "recordings"
    directory.mkdir()
    for case in (LOCK_CASE, HEALTHY_CASE):
        (directory / f"{case.case_id}.json").write_text(
            json.dumps({"case_id": case.case_id, "evidence": evidence_to_dict(EVIDENCE)}))
    return directory


def test_every_model_sees_the_same_evidence_and_the_rule_engine_runs_once(recordings, tmp_path):
    client = FakeClient(GOOD_ANSWER)
    ledger = SpendLedger(tmp_path / "spend.json", cap_usd=40)

    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], client,
                                  models=("claude-opus-5-5", "claude-haiku-5-5"), repeats=2,
                                  ledger=ledger, recordings_directory=recordings)

    assert stop_reason is None
    assert sum(1 for r in results if r.backend == "rule_engine") == 2
    assert sum(1 for r in results if r.backend != "rule_engine") == 2 * 2 * 2
    assert len(client.requests) == 8
    # Calls go case by case, model by model, so a short calibration run
    # reaches every model.
    assert [r["model"] for r in client.requests[:2]] == ["claude-opus-5-5", "claude-haiku-5-5"]
    lock_prompts = [r["messages"][0]["content"] for r in client.requests]
    assert len(set(lock_prompts)) == 2  # one prompt per case, identical across models and repeats


def test_the_cap_holds_against_the_worst_case_not_the_average(recordings, tmp_path):
    client = FakeClient(GOOD_ANSWER, input_tokens=4_000, output_tokens=1_000)
    ledger = SpendLedger(tmp_path / "spend.json", cap_usd=1.0)
    # Worst case for one opus call: 6,000 input (1.5 x the largest seen) and
    # 16,000 output tokens = 0.344 USD. Each real call costs 0.036 USD.
    worst_case = ledger.worst_case_cost("claude-opus-5-5")

    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], client, models=("claude-opus-5-5",),
                                  repeats=50, ledger=ledger, recordings_directory=recordings)

    assert worst_case == pytest.approx((6_000 * 4 + 16_000 * 20) / 1e6)
    assert "risk crossing the cap" in stop_reason
    # It stops while even a full-length answer would still have fitted.
    assert ledger.total_usd + ledger.worst_case_cost("claude-opus-5-5") > 1.0
    assert ledger.total_usd <= 1.0 - 0.036
    # The ledger persists: a new run starts from what was already spent.
    assert SpendLedger(tmp_path / "spend.json", cap_usd=1.0).total_usd == pytest.approx(ledger.total_usd)


def test_a_call_that_fails_without_usage_is_charged_at_its_worst_case(recordings, tmp_path):
    class TimingOutClient:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **request):
            raise TimeoutError("read timed out")

    ledger = SpendLedger(tmp_path / "spend.json", cap_usd=40)

    results, stop_reason = replay([LOCK_CASE], TimingOutClient(), models=("claude-opus-5-5",),
                                  repeats=1, ledger=ledger, recordings_directory=recordings)

    model_result = next(r for r in results if r.backend != "rule_engine")
    assert model_result.case_result.outcome == "diagnosis_error"
    assert ledger.total_usd == pytest.approx(ledger.worst_case_cost("claude-opus-5-5"))
    assert model_result.cost_usd == pytest.approx(ledger.total_usd)
    assert ledger.calls[0]["estimated"] is True


@pytest.mark.parametrize("error_name", ["AuthenticationError", "NotFoundError", "BadRequestError"])
def test_an_error_every_call_would_repeat_skips_each_model_after_one_try(recordings, tmp_path, error_name):
    persistent_error = type(error_name, (Exception,), {})

    class FailingClient:
        def __init__(self):
            self.messages, self.calls = self, 0

        def create(self, **request):
            self.calls += 1
            raise persistent_error("nope")

    client = FailingClient()

    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], client, repeats=5,
                                  ledger=SpendLedger(tmp_path / "s.json"),
                                  recordings_directory=recordings)

    # Owner, 2026-11-22: a provider that errors is skipped and the next one
    # runs. Here every model fails, so each is tried once and then skipped.
    from eval_runner.model_comparison import APPROVED_MODELS
    assert client.calls == len(APPROVED_MODELS)
    assert error_name in stop_reason and "skipped" in stop_reason
    assert not any(r.case_result.outcome == "diagnosis_error" for r in results)


def test_estimate_starts_from_assumptions_then_uses_measured_calls(tmp_path):
    ledger = SpendLedger(tmp_path / "spend.json", cap_usd=40)
    first_estimate = ledger.estimated_next_cost("claude-opus-5-5")

    from diagnosis.llm import ModelCall
    ledger.add(ModelCall("claude-opus-5-5", 10_000, 500, 0.05, 3.0), "case")

    assert first_estimate == pytest.approx((4_000 * 4 + 2_000 * 20) / 1e6)
    assert ledger.estimated_next_cost("claude-opus-5-5") == pytest.approx((10_000 * 4 + 500 * 20) / 1e6)
    with pytest.raises(BudgetExhausted):
        SpendLedger(tmp_path / "other.json", cap_usd=0.01).check_room_for("claude-opus-5-5")


def test_call_limit_stops_after_exactly_that_many_calls(recordings, tmp_path):
    client = FakeClient(GOOD_ANSWER)

    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], client, repeats=5,
                                  ledger=SpendLedger(tmp_path / "s.json"), max_calls=3,
                                  recordings_directory=recordings)

    assert len(client.requests) == 3 and "3 model calls" in stop_reason


def test_failures_count_as_no_answer_and_cases_without_a_recording_are_skipped(recordings, tmp_path):
    missing = CASES["basic-idle-flood"]  # no recording in the fixture

    results, _ = replay([LOCK_CASE, missing], FakeClient("not json"), models=("claude-haiku-5-5",),
                        repeats=1, ledger=SpendLedger(tmp_path / "s.json"),
                        recordings_directory=recordings)

    model_results = [r for r in results if r.backend != "rule_engine"]
    assert [r.case_result.case.case_id for r in model_results] == [LOCK_CASE.case_id]
    assert model_results[0].case_result.outcome == "diagnosis_error"
    assert model_results[0].cost_usd > 0  # the failed call still cost money


def test_stability_and_report(recordings, tmp_path):
    results, _ = replay([LOCK_CASE, HEALTHY_CASE], FakeClient(GOOD_ANSWER),
                        models=("claude-haiku-5-5",), repeats=3,
                        ledger=SpendLedger(tmp_path / "s.json"), recordings_directory=recordings)
    haiku = [r for r in results if r.backend == "claude-haiku-5-5"]

    assert stability(haiku) == (2, 2)
    text = format_replay_report(results)
    assert text.startswith("REPLAY")
    assert "BASIC TIER" in text and "claude-haiku-5-5" in text and "rule_engine" in text
    # Always answers lock_wait: right on the lock case (3/3), a false positive on healthy (3/3).
    haiku_line = next(line for line in text.splitlines() if line.startswith("claude-haiku-5-5"))
    assert "100.0% (3/3)" in haiku_line and "3/3" in haiku_line


def test_calibration_summary_projects_the_full_cost_from_measured_calls(recordings, tmp_path):
    from eval_runner.model_comparison import calibration_summary

    ledger = SpendLedger(tmp_path / "s.json", cap_usd=40)
    replay([LOCK_CASE, HEALTHY_CASE], FakeClient(GOOD_ANSWER, input_tokens=4_000, output_tokens=1_000), repeats=5,
           ledger=ledger, max_calls=4, recordings_directory=recordings,
           models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"))

    summary = calibration_summary(ledger, ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
                                  case_count=22, repeats=5)

    assert "claude-opus-5-5: 2 measured call(s)" in summary
    assert "claude-sonnet-5-5: 1 measured call(s)" in summary
    assert "claude-haiku-5-5: 1 measured call(s)" in summary
    expected = 110 * ((4_000 * 4 + 1_000 * 20) + (4_000 * 2 + 1_000 * 10) + (4_000 * 0.1 + 1_000 * 0.5)) / 1e6
    assert f"{expected:.2f} USD" in summary


def test_recording_keeps_only_valid_cases_and_removes_stale_files(tmp_path, monkeypatch):
    from eval_runner import model_comparison
    from eval_runner.scoring import CaseResult

    directory = tmp_path / "recordings"
    directory.mkdir()
    (directory / f"{LOCK_CASE.case_id}.json").write_text("{}")  # stale from an earlier run

    def fake_run_case(case, diagnose):
        diagnose(case.alert, None)  # captures evidence
        outcome = "invalid" if case.case_id == LOCK_CASE.case_id else "hit"
        return CaseResult(case, "lock_wait", (), outcome, None, None, 1.0, None)

    monkeypatch.setattr(model_comparison, "run_case", fake_run_case)
    monkeypatch.setattr(model_comparison, "collect_evidence", lambda access: EVIDENCE)

    model_comparison.record_cases([LOCK_CASE, HEALTHY_CASE], recordings_directory=directory)

    assert not (directory / f"{LOCK_CASE.case_id}.json").exists()
    assert (directory / f"{HEALTHY_CASE.case_id}.json").exists()
    assert model_comparison.load_recording(HEALTHY_CASE.case_id, directory) == EVIDENCE


def test_api_client_strips_quotes_and_reports_a_missing_file_as_a_missing_key(tmp_path):
    from eval_runner.model_comparison import load_api_client

    with pytest.raises(KeyError):
        load_api_client(tmp_path / "no-such.env")
    env_path = tmp_path / ".env"
    env_path.write_text('ANTHROPIC_API_KEY="test-key-for-unit-test"\n')

    client = load_api_client(env_path)

    assert client.api_key == "test-key-for-unit-test"
    assert client.max_retries == 0
