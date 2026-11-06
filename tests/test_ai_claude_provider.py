"""ClaudeProvider tests with a fake Anthropic client (no network, no API key)."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel, Field

from app.ai import (
    AIRequest,
    AITask,
    ClaudeModelRouting,
    ClaudeProvider,
    ClaudeProviderConnectionError,
    ClaudeProviderPolicyError,
    ClaudeProviderRefusalError,
    ClaudeProviderResponseError,
    ClaudeProviderTimeoutError,
    ClaudeProviderValidationError,
    DataSensitivity,
    ReasoningMode,
    ResponseFormat,
)
from app.ai.claude_provider import FALLBACK_BETA, PROVIDER_NAME
from app.ai.local_provider import LocalProvider
from app.ai.provider_factory import DEFAULT_MODEL, build_provider


class Verdict(BaseModel):
    label: str
    score: float = Field(ge=0, le=1)


def text_response(text="ok", *, model="claude-sonnet-5-5", stop_reason="end_turn", parsed=None, usage=None):
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        stop_details=None,
        content=[SimpleNamespace(type="thinking", thinking="..."), SimpleNamespace(type="text", text=text)],
        parsed_output=parsed,
        usage=usage
        or SimpleNamespace(input_tokens=11, output_tokens=7, cache_read_input_tokens=0, cache_creation_input_tokens=None),
    )


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else text_response()
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def _answer(self, kind, kwargs):
        self.calls.append((kind, kwargs))
        if self.error is not None:
            raise self.error
        return self.response

    def create(self, **kwargs):
        return self._answer("create", kwargs)

    def parse(self, **kwargs):
        return self._answer("parse", kwargs)


def provider(messages: FakeMessages, **kwargs) -> ClaudeProvider:
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return ClaudeProvider(client=client, **kwargs)


def request(**kwargs) -> AIRequest:
    base = {"task": AITask.RESEARCH, "prompt": "Assess NVDA."}
    base.update(kwargs)
    return AIRequest(**base)


_HTTP_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


# --- routing ------------------------------------------------------------------------------


@pytest.mark.parametrize("task", [AITask.RELEVANCE, AITask.TRIAGE, AITask.CLASSIFICATION, AITask.SENTIMENT, AITask.EXTRACTION])
def test_fast_tasks_go_to_the_fast_model_without_server_fallback(task):
    messages = FakeMessages(text_response(model="claude-haiku-5-5"))
    result = provider(messages).infer(request(task=task))
    kind, params = messages.calls[0]
    assert kind == "create"
    assert params["model"] == "claude-haiku-5-5"
    assert params["output_config"] == {"effort": "low"}
    assert params["betas"] == [] and "fallbacks" not in params
    assert result.model == "claude-haiku-5-5"


def test_research_goes_to_the_research_model_with_server_fallback():
    messages = FakeMessages()
    provider(messages).infer(request(reasoning_mode=ReasoningMode.REASONING))
    _, params = messages.calls[0]
    assert params["model"] == "claude-sonnet-5-5"
    assert params["output_config"] == {"effort": "high"}
    assert params["max_tokens"] == 16_000
    assert params["betas"] == [FALLBACK_BETA] and params["fallbacks"] == "default"
    assert params["messages"] == [{"role": "user", "content": "Assess NVDA."}]
    assert "thinking" not in params and "temperature" not in params


def test_server_fallback_can_be_disabled_and_task_overrides_apply():
    routing = ClaudeModelRouting(task_overrides={AITask.RESEARCH: "claude-opus-5-5"})
    messages = FakeMessages()
    provider(messages, routing=routing, server_fallback=False).infer(request())
    _, params = messages.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["betas"] == [] and "fallbacks" not in params


def test_served_model_is_recorded_when_fallback_answered():
    messages = FakeMessages(text_response(model="claude-opus-5-5"))
    assert provider(messages).infer(request()).model == "claude-opus-5-5"


# --- data policy (G8) ---------------------------------------------------------------------


@pytest.mark.parametrize("sensitivity", [DataSensitivity.PORTFOLIO, DataSensitivity.ACCOUNT])
def test_non_public_data_never_reaches_the_client(sensitivity):
    messages = FakeMessages()
    with pytest.raises(ClaudeProviderPolicyError):
        provider(messages).infer(request(sensitivity=sensitivity))
    assert messages.calls == []


# --- text and JSON ------------------------------------------------------------------------


def test_text_response_and_usage_mapping():
    result = provider(FakeMessages(text_response("Bullish."))).infer(request())
    assert result.provider == PROVIDER_NAME
    assert result.content == "Bullish."
    assert result.structured_output is None
    assert result.usage == {"prompt_tokens": 11, "completion_tokens": 7, "cache_read_tokens": 0}
    assert result.latency_ms >= 0


def test_empty_text_is_an_error():
    with pytest.raises(ClaudeProviderResponseError):
        provider(FakeMessages(text_response("  "))).infer(request())


def test_schema_request_uses_structured_outputs_and_revalidates():
    parsed = Verdict(label="UP", score=0.7)
    messages = FakeMessages(text_response('{"label":"UP","score":0.7}', parsed=parsed))
    result = provider(messages).infer(request(response_format=ResponseFormat.JSON, output_schema=Verdict))
    kind, params = messages.calls[0]
    assert kind == "parse" and params["output_format"] is Verdict
    assert result.structured_output == {"label": "UP", "score": 0.7}


def test_schema_request_without_parsed_output_fails():
    messages = FakeMessages(text_response("{}", parsed=None))
    with pytest.raises(ClaudeProviderResponseError):
        provider(messages).infer(request(response_format=ResponseFormat.JSON, output_schema=Verdict))


def test_schema_revalidation_failure_is_typed():
    messages = FakeMessages(text_response("{}", parsed={"label": "UP", "score": 3}))
    with pytest.raises(ClaudeProviderValidationError):
        provider(messages).infer(request(response_format=ResponseFormat.JSON, output_schema=Verdict))


def test_parse_time_validation_failure_is_typed():
    try:
        Verdict.model_validate({"label": "UP", "score": 3})
    except Exception as exc:  # pydantic.ValidationError
        error = exc
    with pytest.raises(ClaudeProviderValidationError):
        provider(FakeMessages(error=error)).infer(request(response_format=ResponseFormat.JSON, output_schema=Verdict))


def test_json_without_schema_asks_for_json_and_parses_it():
    messages = FakeMessages(text_response('{"a": 1}'))
    result = provider(messages).infer(request(response_format=ResponseFormat.JSON))
    kind, params = messages.calls[0]
    assert kind == "create"
    assert params["messages"][0]["content"].endswith("Return only one JSON object, with no other text.")
    assert result.structured_output == {"a": 1}


@pytest.mark.parametrize("text", ["not json", "[1, 2]"])
def test_json_without_schema_rejects_non_objects(text):
    with pytest.raises(ClaudeProviderResponseError):
        provider(FakeMessages(text_response(text))).infer(request(response_format=ResponseFormat.JSON))


# --- stop reasons and transport errors ----------------------------------------------------


def test_refusal_is_typed_and_nothing_is_invented():
    response = text_response("")
    response.stop_reason = "refusal"
    response.stop_details = SimpleNamespace(category="cyber")
    with pytest.raises(ClaudeProviderRefusalError, match="category=cyber"):
        provider(FakeMessages(response)).infer(request())


def test_truncation_is_an_error():
    with pytest.raises(ClaudeProviderResponseError, match="truncated"):
        provider(FakeMessages(text_response("partial", stop_reason="max_tokens"))).infer(request())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (anthropic.APITimeoutError(request=_HTTP_REQUEST), ClaudeProviderTimeoutError),
        (anthropic.APIConnectionError(request=_HTTP_REQUEST), ClaudeProviderConnectionError),
        (
            anthropic.InternalServerError(
                "overloaded", response=httpx.Response(529, request=_HTTP_REQUEST), body=None
            ),
            ClaudeProviderResponseError,
        ),
    ],
)
def test_transport_errors_are_mapped(error, expected):
    with pytest.raises(expected):
        provider(FakeMessages(error=error)).infer(request())


def test_timeout_must_be_positive():
    with pytest.raises(ValueError):
        ClaudeProvider(client=object(), timeout_seconds=0)


# --- factory ------------------------------------------------------------------------------


def test_factory_defaults_to_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    built = build_provider()
    assert isinstance(built, ClaudeProvider)
    assert built.model_name == DEFAULT_MODEL == "claude-sonnet-5-5"
    assert built.routing.fast_model == "claude-haiku-5-5"
    assert build_provider("claude-opus-5-5").model_name == "claude-opus-5-5"


def test_factory_keeps_ollama_only_behind_an_explicit_prefix():
    assert isinstance(build_provider("ollama:qwen3:8b"), LocalProvider)
    with pytest.raises(ValueError):
        build_provider("qwen3:8b")
