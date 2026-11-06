import pytest
from pydantic import ValidationError

from app.ai.models import (
    AIRequest,
    AIResponse,
    AITask,
    DataSensitivity,
    ReasoningMode,
    ResponseFormat,
)


def test_ai_request_defaults_are_safe_for_public_fast_text_tasks():
    request = AIRequest(
        task=AITask.SENTIMENT,
        prompt="Classify this financial headline.",
    )

    assert request.sensitivity is DataSensitivity.PUBLIC
    assert request.reasoning_mode is ReasoningMode.FAST
    assert request.response_format is ResponseFormat.TEXT
    assert request.metadata == {}


def test_ai_request_supports_private_reasoning_json_request():
    request = AIRequest(
        task=AITask.RESEARCH,
        prompt="Assess the evidence.",
        sensitivity=DataSensitivity.PORTFOLIO,
        reasoning_mode=ReasoningMode.REASONING,
        response_format=ResponseFormat.JSON,
        metadata={"ticker": "NVDA"},
    )

    assert request.sensitivity is DataSensitivity.PORTFOLIO
    assert request.reasoning_mode is ReasoningMode.REASONING
    assert request.response_format is ResponseFormat.JSON
    assert request.metadata["ticker"] == "NVDA"


def test_ai_request_rejects_blank_prompt():
    with pytest.raises(ValidationError):
        AIRequest(task=AITask.SENTIMENT, prompt="   ")


def test_ai_request_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        AIRequest(
            task=AITask.SENTIMENT,
            prompt="Headline",
            ollama_think=False,
        )


def test_ai_response_accepts_text_output():
    response = AIResponse(
        provider="LOCAL_OLLAMA",
        model="qwen3:8b",
        content="NEGATIVE",
        latency_ms=125.0,
        usage={"prompt_tokens": 20, "completion_tokens": 1},
    )

    assert response.content == "NEGATIVE"
    assert response.structured_output is None


def test_ai_response_accepts_structured_output_without_text():
    response = AIResponse(
        provider="LOCAL_OLLAMA",
        model="qwen3:8b",
        structured_output={"sentiment": "NEGATIVE"},
        latency_ms=125.0,
    )

    assert response.structured_output == {"sentiment": "NEGATIVE"}


def test_ai_response_requires_text_or_structured_output():
    with pytest.raises(ValidationError):
        AIResponse(
            provider="LOCAL_OLLAMA",
            model="qwen3:8b",
            latency_ms=10.0,
        )


def test_ai_response_rejects_negative_latency():
    with pytest.raises(ValidationError):
        AIResponse(
            provider="LOCAL_OLLAMA",
            model="qwen3:8b",
            content="NEUTRAL",
            latency_ms=-1.0,
        )
