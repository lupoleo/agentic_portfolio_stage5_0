import json

import pytest
from pydantic import ValidationError

from app.ai import (
    AIRequest,
    AITask,
    FinancialSentimentResult,
    LocalProvider,
    LocalProviderValidationError,
    ResponseFormat,
    SentimentLabel,
)


def test_financial_sentiment_schema_accepts_valid_result():
    result = FinancialSentimentResult(
        sentiment=SentimentLabel.NEGATIVE,
        confidence=0.95,
        requires_deep_research=True,
    )

    assert result.sentiment is SentimentLabel.NEGATIVE
    assert result.confidence == 0.95
    assert result.requires_deep_research is True


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_financial_sentiment_schema_rejects_invalid_confidence(
    confidence,
):
    with pytest.raises(ValidationError):
        FinancialSentimentResult(
            sentiment=SentimentLabel.NEUTRAL,
            confidence=confidence,
            requires_deep_research=False,
        )


def test_financial_sentiment_schema_rejects_unknown_sentiment():
    with pytest.raises(ValidationError):
        FinancialSentimentResult(
            sentiment="VERY_BAD",
            confidence=0.8,
            requires_deep_research=True,
        )


def test_ai_request_schema_requires_json_response_format():
    with pytest.raises(
        ValidationError,
        match="output_schema requires response_format=JSON",
    ):
        AIRequest(
            task=AITask.CLASSIFICATION,
            prompt="Classify this.",
            output_schema=FinancialSentimentResult,
        )


def test_typed_request_sends_json_schema_to_ollama():
    captured = {}

    def transport(url, body, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return {
            "response": (
                '{"sentiment":"NEGATIVE","confidence":0.95,'
                '"requires_deep_research":true}'
            )
        }

    provider = LocalProvider(transport=transport)
    response = provider.infer(
        AIRequest(
            task=AITask.CLASSIFICATION,
            prompt="Classify this.",
            response_format=ResponseFormat.JSON,
            output_schema=FinancialSentimentResult,
        )
    )

    ollama_format = captured["payload"]["format"]

    assert isinstance(ollama_format, dict)
    assert ollama_format["type"] == "object"
    assert "sentiment" in ollama_format["properties"]
    assert "confidence" in ollama_format["properties"]
    assert "requires_deep_research" in ollama_format["properties"]

    assert response.structured_output == {
        "sentiment": "NEGATIVE",
        "confidence": 0.95,
        "requires_deep_research": True,
    }


def test_typed_request_rejects_invalid_sentiment_from_model():
    def transport(url, body, timeout):
        return {
            "response": (
                '{"sentiment":"VERY_BAD","confidence":0.95,'
                '"requires_deep_research":true}'
            )
        }

    provider = LocalProvider(transport=transport)

    with pytest.raises(
        LocalProviderValidationError,
        match="FinancialSentimentResult validation",
    ):
        provider.infer(
            AIRequest(
                task=AITask.CLASSIFICATION,
                prompt="Classify this.",
                response_format=ResponseFormat.JSON,
                output_schema=FinancialSentimentResult,
            )
        )


def test_typed_request_rejects_out_of_range_confidence():
    def transport(url, body, timeout):
        return {
            "response": (
                '{"sentiment":"NEGATIVE","confidence":95,'
                '"requires_deep_research":true}'
            )
        }

    provider = LocalProvider(transport=transport)

    with pytest.raises(LocalProviderValidationError):
        provider.infer(
            AIRequest(
                task=AITask.CLASSIFICATION,
                prompt="Classify this.",
                response_format=ResponseFormat.JSON,
                output_schema=FinancialSentimentResult,
            )
        )


def test_output_schema_is_not_serialized_as_request_metadata():
    request = AIRequest(
        task=AITask.CLASSIFICATION,
        prompt="Classify this.",
        response_format=ResponseFormat.JSON,
        output_schema=FinancialSentimentResult,
    )

    dumped = request.model_dump()

    assert "output_schema" not in dumped
