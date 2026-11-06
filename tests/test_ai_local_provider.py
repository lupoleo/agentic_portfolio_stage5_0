import json

import pytest

from app.ai.local_provider import (
    LocalProvider,
    LocalProviderResponseError,
)
from app.ai.models import (
    AIRequest,
    AITask,
    ReasoningMode,
    ResponseFormat,
)


def test_local_provider_defaults():
    provider = LocalProvider()

    assert provider.provider_name == "LOCAL_OLLAMA"
    assert provider.model_name == "qwen3:8b"
    assert provider.base_url == "http://localhost:11434"
    assert provider.timeout_seconds == 60.0
    assert provider.fast_num_predict == 512
    assert provider.reasoning_num_predict == 2048
    assert provider.structured_reasoning_num_predict == 4096
    assert provider.research_reasoning_num_predict == 12288


def test_fast_request_maps_to_ollama_think_false():
    captured = {}

    def transport(url, body, timeout):
        captured["url"] = url
        captured["payload"] = json.loads(body.decode("utf-8"))
        captured["timeout"] = timeout
        return {
            "response": "NEGATIVE",
            "prompt_eval_count": 12,
            "eval_count": 1,
        }

    provider = LocalProvider(transport=transport)
    response = provider.infer(
        AIRequest(
            task=AITask.SENTIMENT,
            prompt="Company cuts guidance.",
        )
    )

    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["payload"]["model"] == "qwen3:8b"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["think"] is False
    assert captured["payload"]["options"] == {
        "num_predict": 512,
    }
    assert "format" not in captured["payload"]
    assert captured["timeout"] == 60.0

    assert response.provider == "LOCAL_OLLAMA"
    assert response.model == "qwen3:8b"
    assert response.content == "NEGATIVE"
    assert response.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 1,
    }
    assert response.latency_ms >= 0.0


def test_reasoning_request_maps_to_ollama_think_true():
    captured = {}

    def transport(url, body, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return {"response": "Detailed assessment."}

    provider = LocalProvider(transport=transport)
    provider.infer(
        AIRequest(
            task=AITask.REASONING,
            prompt="Assess this event.",
            reasoning_mode=ReasoningMode.REASONING,
        )
    )

    assert captured["payload"]["think"] is True
    assert captured["payload"]["options"] == {
        "num_predict": 2048,
    }


def test_generation_budgets_are_configurable():
    captured = {}

    def transport(url, body, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return {"response": "Detailed assessment."}

    provider = LocalProvider(
        fast_num_predict=256,
        reasoning_num_predict=1024,
        structured_reasoning_num_predict=3072,
        transport=transport,
    )

    provider.infer(
        AIRequest(
            task=AITask.REASONING,
            prompt="Assess this event.",
            reasoning_mode=ReasoningMode.REASONING,
        )
    )

    assert captured["payload"]["options"] == {
        "num_predict": 1024,
    }


def test_structured_reasoning_uses_dedicated_generation_budget():
    captured = {}

    def transport(url, body, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return {"response": '{"status":"ok"}'}

    provider = LocalProvider(
        structured_reasoning_num_predict=3072,
        transport=transport,
    )

    provider.infer(
        AIRequest(
            task=AITask.REASONING,
            prompt="Assess this event.",
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
        )
    )

    assert captured["payload"]["think"] is True
    assert captured["payload"]["options"] == {
        "num_predict": 3072,
    }


def test_research_structured_reasoning_uses_dedicated_generation_budget():
    captured = {}

    def transport(url, body, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return {"response": '{"status":"ok"}'}

    provider = LocalProvider(
        research_reasoning_num_predict=6144,
        transport=transport,
    )

    provider.infer(
        AIRequest(
            task=AITask.RESEARCH,
            prompt="Research this opportunity.",
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
        )
    )

    assert captured["payload"]["think"] is True
    assert captured["payload"]["options"] == {
        "num_predict": 6144,
    }


def test_non_research_structured_reasoning_keeps_standard_budget():
    captured = {}

    def transport(url, body, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return {"response": '{"status":"ok"}'}

    provider = LocalProvider(
        structured_reasoning_num_predict=3072,
        research_reasoning_num_predict=6144,
        transport=transport,
    )

    provider.infer(
        AIRequest(
            task=AITask.REASONING,
            prompt="Score this opportunity.",
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
        )
    )

    assert captured["payload"]["options"] == {
        "num_predict": 3072,
    }



def test_incomplete_research_json_done_false_gets_one_compact_retry():
    captured = []

    def transport(url, body, timeout):
        payload = json.loads(body.decode("utf-8"))
        captured.append(payload)
        if len(captured) == 1:
            return {
                "response": '{"market_context":"unterminated',
                "done": False,
            }
        return {
            "response": '{"status":"ok"}',
            "done": True,
            "eval_count": 20,
        }

    provider = LocalProvider(transport=transport)
    response = provider.infer(
        AIRequest(
            task=AITask.RESEARCH,
            prompt="Research.",
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
        )
    )

    assert len(captured) == 2
    assert captured[0]["think"] is True
    assert captured[0]["options"]["num_predict"] == 12288
    assert captured[1]["think"] is False
    assert captured[1]["options"]["num_predict"] == 6144
    assert "CRITICAL COMPACT RETRY" in captured[1]["prompt"]
    assert response.structured_output == {"status": "ok"}
    assert response.usage["transport_retries"] == 1


def test_length_truncated_research_json_gets_one_compact_retry():
    calls = 0

    def transport(url, body, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "response": '{"market_context":"unterminated',
                "done": True,
                "done_reason": "length",
                "eval_count": 12288,
            }
        return {
            "response": '{"status":"ok"}',
            "done": True,
        }

    provider = LocalProvider(transport=transport)
    response = provider.infer(
        AIRequest(
            task=AITask.RESEARCH,
            prompt="Research.",
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
        )
    )

    assert calls == 2
    assert response.structured_output == {"status": "ok"}


def test_json_request_maps_to_ollama_json_format_and_parses_output():
    captured = {}

    def transport(url, body, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return {
            "response": (
                '{"sentiment":"NEGATIVE","confidence":0.91}'
            )
        }

    provider = LocalProvider(transport=transport)
    response = provider.infer(
        AIRequest(
            task=AITask.CLASSIFICATION,
            prompt="Classify this headline.",
            response_format=ResponseFormat.JSON,
        )
    )

    assert captured["payload"]["format"] == "json"
    assert response.structured_output == {
        "sentiment": "NEGATIVE",
        "confidence": 0.91,
    }


def test_json_request_rejects_invalid_structured_json():
    def transport(url, body, timeout):
        return {
            "response": "not-json",
            "done": True,
            "done_reason": "length",
            "prompt_eval_count": 321,
            "eval_count": 4096,
        }

    provider = LocalProvider(transport=transport)

    with pytest.raises(
        LocalProviderResponseError,
        match=(
            r"invalid structured JSON .*"
            r"done=True.*"
            r"done_reason='length'.*"
            r"prompt_eval_count=321.*"
            r"eval_count=4096.*"
            r"response_chars=8"
        ),
    ):
        provider.infer(
            AIRequest(
                task=AITask.CLASSIFICATION,
                prompt="Classify this headline.",
                response_format=ResponseFormat.JSON,
            )
        )


def test_json_request_requires_json_object():
    def transport(url, body, timeout):
        return {"response": '["NEGATIVE"]'}

    provider = LocalProvider(transport=transport)

    with pytest.raises(
        LocalProviderResponseError,
        match="must be a JSON object",
    ):
        provider.infer(
            AIRequest(
                task=AITask.CLASSIFICATION,
                prompt="Classify this headline.",
                response_format=ResponseFormat.JSON,
            )
        )


def test_provider_rejects_ollama_error_payload():
    def transport(url, body, timeout):
        return {"error": "model not found"}

    provider = LocalProvider(transport=transport)

    with pytest.raises(
        LocalProviderResponseError,
        match="model not found",
    ):
        provider.infer(
            AIRequest(
                task=AITask.SENTIMENT,
                prompt="Headline",
            )
        )


def test_provider_rejects_missing_response_field():
    def transport(url, body, timeout):
        return {"done": True}

    provider = LocalProvider(transport=transport)

    with pytest.raises(
        LocalProviderResponseError,
        match="missing string field",
    ):
        provider.infer(
            AIRequest(
                task=AITask.SENTIMENT,
                prompt="Headline",
            )
        )


def test_provider_rejects_empty_text_response():
    def transport(url, body, timeout):
        return {"response": "   "}

    provider = LocalProvider(transport=transport)

    with pytest.raises(
        LocalProviderResponseError,
        match="empty text response",
    ):
        provider.infer(
            AIRequest(
                task=AITask.SENTIMENT,
                prompt="Headline",
            )
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model_name": "   "}, "model_name"),
        ({"base_url": "   "}, "base_url"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": -1}, "timeout_seconds"),
        ({"fast_num_predict": 0}, "fast_num_predict"),
        ({"fast_num_predict": -1}, "fast_num_predict"),
        ({"reasoning_num_predict": 0}, "reasoning_num_predict"),
        ({"reasoning_num_predict": -1}, "reasoning_num_predict"),
        (
            {"structured_reasoning_num_predict": 0},
            "structured_reasoning_num_predict",
        ),
        (
            {"structured_reasoning_num_predict": -1},
            "structured_reasoning_num_predict",
        ),
        (
            {"research_reasoning_num_predict": 0},
            "research_reasoning_num_predict",
        ),
        (
            {"research_reasoning_num_predict": -1},
            "research_reasoning_num_predict",
        ),
    ],
)
def test_provider_rejects_invalid_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        LocalProvider(**kwargs)
