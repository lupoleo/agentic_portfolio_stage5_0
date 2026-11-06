from datetime import datetime, timezone

from app.ai import (
    AIRequest,
    AIResponse,
    AITask,
    AIValidationStatus,
    DataSensitivity,
    FinancialSentimentResult,
    ReasoningMode,
    ResponseFormat,
    build_inference_record,
)


def test_build_inference_record_for_typed_output():
    ai_request = AIRequest(
        task=AITask.CLASSIFICATION,
        prompt="Classify the financial sentiment.",
        sensitivity=DataSensitivity.PUBLIC,
        reasoning_mode=ReasoningMode.FAST,
        response_format=ResponseFormat.JSON,
        output_schema=FinancialSentimentResult,
        metadata={"ticker": "XYZ"},
    )
    ai_response = AIResponse(
        provider="LOCAL_OLLAMA",
        model="qwen3:8b",
        content=(
            '{"sentiment":"NEGATIVE","confidence":0.95,'
            '"requires_deep_research":false}'
        ),
        structured_output={
            "sentiment": "NEGATIVE",
            "confidence": 0.95,
            "requires_deep_research": False,
        },
        latency_ms=123.4,
        usage={"prompt_tokens": 38, "completion_tokens": 31},
    )
    timestamp = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)

    record = build_inference_record(
        ai_request,
        ai_response,
        prompt_version="financial-sentiment-v1",
        evidence_ids=["NEWS-001", "NEWS-002"],
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        timestamp=timestamp,
        inference_id="AI-TEST-001",
    )

    assert record.inference_id == "AI-TEST-001"
    assert record.timestamp == timestamp
    assert record.task is AITask.CLASSIFICATION
    assert record.provider == "LOCAL_OLLAMA"
    assert record.model == "qwen3:8b"
    assert record.prompt_version == "financial-sentiment-v1"
    assert record.evidence_ids == ["NEWS-001", "NEWS-002"]
    assert record.portfolio_snapshot_id == "SNAP-001"
    assert record.risk_state_id == "RISK-001"
    assert record.request_metadata == {"ticker": "XYZ"}
    assert record.validation_status is AIValidationStatus.VALIDATED
    assert record.validated_output == ai_response.structured_output
    assert record.latency_ms == 123.4
    assert record.usage["completion_tokens"] == 31


def test_build_inference_record_hashes_prompt_without_storing_prompt():
    ai_request = AIRequest(
        task=AITask.SENTIMENT,
        prompt="Sensitive prompt body",
    )
    ai_response = AIResponse(
        provider="LOCAL_OLLAMA",
        model="qwen3:8b",
        content="NEGATIVE",
        latency_ms=10.0,
    )

    record = build_inference_record(
        ai_request,
        ai_response,
        inference_id="AI-TEST-002",
    )
    dumped = record.model_dump()

    assert len(record.prompt_sha256) == 64
    assert "prompt" not in dumped
    assert "Sensitive prompt body" not in str(dumped)


def test_untyped_text_inference_is_not_marked_validated():
    ai_request = AIRequest(
        task=AITask.SENTIMENT,
        prompt="Classify this headline.",
        response_format=ResponseFormat.TEXT,
    )
    ai_response = AIResponse(
        provider="LOCAL_OLLAMA",
        model="qwen3:8b",
        content="NEGATIVE",
        latency_ms=25.0,
    )

    record = build_inference_record(
        ai_request,
        ai_response,
        inference_id="AI-TEST-003",
    )

    assert (
        record.validation_status
        is AIValidationStatus.NOT_APPLICABLE
    )
    assert record.validated_output is None


def test_generated_record_has_id_and_timezone_aware_timestamp():
    ai_request = AIRequest(
        task=AITask.SENTIMENT,
        prompt="Headline",
    )
    ai_response = AIResponse(
        provider="LOCAL_OLLAMA",
        model="qwen3:8b",
        content="NEUTRAL",
        latency_ms=1.0,
    )

    record = build_inference_record(ai_request, ai_response)

    assert record.inference_id.startswith("AI-")
    assert record.timestamp.tzinfo is not None
    assert record.timestamp.utcoffset() is not None


def test_builder_copies_mutable_request_and_response_data():
    metadata = {"ticker": "XYZ"}
    usage = {"prompt_tokens": 10}
    evidence_ids = ["NEWS-001"]

    ai_request = AIRequest(
        task=AITask.SENTIMENT,
        prompt="Headline",
        metadata=metadata,
    )
    ai_response = AIResponse(
        provider="LOCAL_OLLAMA",
        model="qwen3:8b",
        content="NEGATIVE",
        latency_ms=1.0,
        usage=usage,
    )

    record = build_inference_record(
        ai_request,
        ai_response,
        evidence_ids=evidence_ids,
        inference_id="AI-TEST-004",
    )

    evidence_ids.append("NEWS-002")
    ai_request.metadata["ticker"] = "CHANGED"
    ai_response.usage["prompt_tokens"] = 999

    assert record.evidence_ids == ["NEWS-001"]
    assert record.request_metadata == {"ticker": "XYZ"}
    assert record.usage == {"prompt_tokens": 10}
