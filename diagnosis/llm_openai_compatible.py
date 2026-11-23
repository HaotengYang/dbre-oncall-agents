"""A second model backend: any provider with an OpenAI-compatible chat API.

Used for Gemini through Google's OpenAI-compatible endpoint. Same system
prompt, same fenced evidence, same answer schema and the same parser as the
Claude backend (diagnosis/llm.py), so the answers are compared like for like.
"""

import time

from diagnosis.llm import (
    ANSWER_SCHEMA,
    EFFORT,
    MAX_OUTPUT_TOKENS,
    SYSTEM_PROMPT,
    ModelAnswerError,
    ModelCall,
    cost_of,
    parse_answer,
    render_evidence,
)
from diagnosis.contract import Diagnosis

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def ask_openai_compatible(client, model, alert_text, evidence):
    """One call. Returns (Diagnosis, ModelCall). Raises ModelAnswerError."""
    started_at = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        reasoning_effort=EFFORT,
        response_format={"type": "json_schema",
                         "json_schema": {"name": "diagnosis", "schema": ANSWER_SCHEMA}},
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": render_evidence(alert_text, evidence)}],
    )
    seconds = time.monotonic() - started_at
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    # Thinking tokens are billed as output; some providers leave them out of
    # completion_tokens, so output is taken as total minus prompt.
    total_tokens = usage.total_tokens if usage and usage.total_tokens else 0
    output_tokens = max(total_tokens - input_tokens, usage.completion_tokens or 0) if usage else 0
    call = ModelCall(model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                     cost_usd=cost_of(model, input_tokens, output_tokens), seconds=seconds)
    choice = response.choices[0] if response.choices else None
    if choice is None:
        raise ModelAnswerError("the response has no choices", call)
    if choice.finish_reason == "length":
        raise ModelAnswerError("the answer was cut off at max_tokens", call)
    if choice.finish_reason == "content_filter":
        raise ModelAnswerError("the provider filtered the answer (content_filter)", call)
    answer_text = choice.message.content if choice.message else None
    if not answer_text:
        raise ModelAnswerError("the response has no text", call)
    try:
        diagnosis = parse_answer(answer_text)
    except ModelAnswerError as invalid:
        raise ModelAnswerError(str(invalid), call) from None
    return Diagnosis(**{**diagnosis.__dict__, "onset": evidence.onset}), call


__all__ = ["GEMINI_OPENAI_BASE_URL", "ask_openai_compatible"]
