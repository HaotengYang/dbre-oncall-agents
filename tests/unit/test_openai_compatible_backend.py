"""The OpenAI-compatible backend (Gemini), rate-limit waits, calibration, key routing."""

import json
from types import SimpleNamespace

import pytest

from diagnosis.llm import ModelAnswerError, cost_of
from diagnosis.llm_openai_compatible import ask_openai_compatible
from eval_runner import model_comparison
from eval_runner.model_comparison import ModelClients, SpendLedger, replay
from tests.unit.test_llm_backend import EVIDENCE, GOOD_ANSWER, FakeClient
from tests.unit.test_model_comparison import HEALTHY_CASE, LOCK_CASE, recordings  # noqa: F401


class FakeChatClient:
    def __init__(self, content, finish_reason="stop", prompt_tokens=4_000,
                 completion_tokens=300, total_tokens=5_000):
        self.sent = []
        self.content, self.finish_reason = content, finish_reason
        self.usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                     total_tokens=total_tokens)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.sent.append(request)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message,
                                                        finish_reason=self.finish_reason)],
                               usage=self.usage)


def test_the_same_prompt_and_schema_go_out_and_the_answer_is_parsed():
    client = FakeChatClient(json.dumps(GOOD_ANSWER))
    diagnosis, call = ask_openai_compatible(client, "gemini-3.8-flash", "alert", EVIDENCE)
    request = client.sent[0]
    assert request["messages"][0]["role"] == "system"
    assert "UNTRUSTED_DATA" in request["messages"][1]["content"]
    assert request["response_format"]["type"] == "json_schema"
    assert diagnosis.root_cause == "lock_wait"
    # Thinking tokens count as output: total 5,000 minus prompt 4,000.
    assert (call.input_tokens, call.output_tokens) == (4_000, 1_000)
    assert call.cost_usd == pytest.approx(cost_of("gemini-3.8-flash", 4_000, 1_000))


@pytest.mark.parametrize("finish_reason, content, message", [
    ("length", json.dumps(GOOD_ANSWER), "cut off"),
    ("content_filter", json.dumps(GOOD_ANSWER), "filtered"),
    ("stop", "", "no text"),
    ("stop", "not json", "not valid JSON"),
])
def test_unusable_answers_raise_with_the_call_so_its_cost_is_counted(finish_reason, content, message):
    client = FakeChatClient(content, finish_reason=finish_reason)
    with pytest.raises(ModelAnswerError, match=message) as raised:
        ask_openai_compatible(client, "gemini-3.8-flash", "alert", EVIDENCE)
    assert raised.value.call is not None and raised.value.call.cost_usd > 0


class RateLimitError(Exception):
    pass


class LimitedThenGood:
    """Refuses twice with a rate limit, then answers."""

    def __init__(self):
        self.attempts = 0
        self.inner = FakeClient(GOOD_ANSWER)

    def ask(self, model, alert_text, evidence):
        self.attempts += 1
        if self.attempts <= 2:
            raise RateLimitError("429")
        return model_comparison.ask_model(self.inner, model, alert_text, evidence)


def test_a_rate_limit_is_waited_out_and_not_counted_as_a_failure(recordings, tmp_path):  # noqa: F811
    waits = []
    ledger = SpendLedger(tmp_path / "s.json", cap_usd=10)
    results, stop_reason = replay([LOCK_CASE], LimitedThenGood(), models=("claude-haiku-5-5",),
                                  repeats=1, ledger=ledger, recordings_directory=recordings,
                                  sleep=waits.append)
    model_results = [r for r in results if r.backend == "claude-haiku-5-5"]
    assert stop_reason is None and waits == [5, 10]
    assert [r.case_result.outcome for r in model_results] == ["hit"]
    assert len(ledger.calls) == 1  # the refused attempts cost nothing


def test_calibration_stops_the_run_when_the_rest_would_cross_the_cap(recordings, tmp_path):  # noqa: F811
    # 4,000 in / 1,000 out on Opus is 0.036 USD a call; 2 cases x 200 repeats
    # would be about 14 USD, over a 10 USD cap.
    ledger = SpendLedger(tmp_path / "s.json", cap_usd=10)
    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], FakeClient(GOOD_ANSWER),
                                  models=("claude-opus-5-5",), repeats=200, ledger=ledger,
                                  recordings_directory=recordings, calibration_calls=10)
    assert "cost calibration after 10 claude-opus-5-5 calls" in stop_reason
    assert len([r for r in results if r.backend == "claude-opus-5-5"]) == 10


def test_calibration_lets_an_affordable_run_finish(recordings, tmp_path):  # noqa: F811
    ledger = SpendLedger(tmp_path / "s.json", cap_usd=10)
    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], FakeClient(GOOD_ANSWER),
                                  models=("claude-haiku-5-5",), repeats=6, ledger=ledger,
                                  recordings_directory=recordings, calibration_calls=10)
    assert stop_reason is None
    assert len([r for r in results if r.backend == "claude-haiku-5-5"]) == 12


def test_models_are_routed_by_provider_and_a_missing_key_makes_them_unavailable(tmp_path):
    clients = ModelClients(anthropic_client=object(), gemini_client=None)
    assert clients.available("claude-haiku-5-5") and not clients.available("gemini-3.8-flash")
    env_path = tmp_path / ".env"
    env_path.write_text("MYSQL_PASSWORD=x\n")
    loaded = model_comparison.load_model_clients(env_path)
    assert not loaded.available("claude-haiku-5-5") and not loaded.available("gemini-3.8-flash")


def test_the_approved_list_and_cap_are_the_owners():
    assert model_comparison.APPROVED_MODELS == (
        "gemini-3.8-flash", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
    assert model_comparison.SPENDING_CAP_USD == 10.00


class OverloadedServerError(Exception):
    status_code = 503


class TimeoutLike(Exception):
    status_code = None


def test_an_overloaded_model_is_waited_out_but_a_timeout_is_not_retried():
    from eval_runner.model_comparison import _is_wait_and_retry
    assert _is_wait_and_retry(OverloadedServerError("high demand"))
    assert _is_wait_and_retry(RateLimitError("429"))
    assert not _is_wait_and_retry(TimeoutLike("timed out"))


def test_a_response_without_usage_is_charged_at_its_worst_case_not_as_free(recordings, tmp_path):  # noqa: F811
    client = FakeChatClient(json.dumps(GOOD_ANSWER))
    client.usage = None
    ledger = SpendLedger(tmp_path / "s.json", cap_usd=10)
    replay([LOCK_CASE], ModelClients(gemini_client=client), models=("gemini-3.8-flash",),
           repeats=1, ledger=ledger, recordings_directory=recordings)
    assert len(ledger.calls) == 1 and ledger.calls[0]["estimated"]
    assert ledger.calls[0]["cost_usd"] == pytest.approx(ledger.worst_case_cost("gemini-3.8-flash"))


class NotFoundError(Exception):
    pass


class GeminiBrokenClaudeFine:
    def __init__(self):
        self.inner = FakeClient(GOOD_ANSWER)

    def ask(self, model, alert_text, evidence):
        if model.startswith("gemini-"):
            raise NotFoundError("unknown model")
        return model_comparison.ask_model(self.inner, model, alert_text, evidence)


def test_a_persistent_error_skips_that_model_and_the_next_one_still_runs(recordings, tmp_path):  # noqa: F811
    ledger = SpendLedger(tmp_path / "s.json", cap_usd=10)
    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], GeminiBrokenClaudeFine(),
                                  models=("gemini-3.8-flash", "claude-haiku-5-5"), repeats=2,
                                  ledger=ledger, recordings_directory=recordings)
    assert [r for r in results if r.backend == "gemini-3.8-flash"] == []
    assert len([r for r in results if r.backend == "claude-haiku-5-5"]) == 4
    assert "gemini-3.8-flash: NotFoundError" in stop_reason and "skipped" in stop_reason


class AlwaysLimited:
    def ask(self, model, alert_text, evidence):
        raise RateLimitError("429 quota")


def test_a_rate_limit_that_never_lifts_skips_the_model_and_charges_nothing(recordings, tmp_path):  # noqa: F811
    waits = []
    ledger = SpendLedger(tmp_path / "s.json", cap_usd=10)
    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], AlwaysLimited(),
                                  models=("gemini-3.8-flash",), repeats=1, ledger=ledger,
                                  recordings_directory=recordings, sleep=waits.append)
    assert ledger.calls == [] and ledger.total_usd == 0
    assert [r for r in results if r.backend == "gemini-3.8-flash"] == []
    assert len(waits) == len(model_comparison.RATE_LIMIT_WAITS_SECONDS)  # gave up once, then skipped
    assert "skipped, nothing charged" in stop_reason


def test_gemini_models_are_sent_through_the_openai_compatible_client(recordings, tmp_path):  # noqa: F811
    client = FakeChatClient(json.dumps(GOOD_ANSWER))
    ledger = SpendLedger(tmp_path / "s.json", cap_usd=10)
    results, _ = replay([LOCK_CASE], ModelClients(gemini_client=client), models=("gemini-3.8-flash",),
                        repeats=1, ledger=ledger, recordings_directory=recordings)
    assert len(client.sent) == 1 and client.sent[0]["model"] == "gemini-3.8-flash"
    assert [r.case_result.outcome for r in results if r.backend == "gemini-3.8-flash"] == ["hit"]


def test_an_answer_longer_than_the_assumed_output_cap_stops_the_run(recordings, tmp_path):  # noqa: F811
    client = FakeChatClient(json.dumps(GOOD_ANSWER), prompt_tokens=4_000, total_tokens=4_000 + 20_000)
    ledger = SpendLedger(tmp_path / "s.json", cap_usd=10)
    results, stop_reason = replay([LOCK_CASE, HEALTHY_CASE], ModelClients(gemini_client=client),
                                  models=("gemini-3.8-flash",), repeats=1, ledger=ledger,
                                  recordings_directory=recordings)
    assert "above the 16000" in stop_reason and len(ledger.calls) == 1
