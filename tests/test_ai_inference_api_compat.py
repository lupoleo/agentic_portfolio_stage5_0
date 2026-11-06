from datetime import datetime, timezone

from app.ai.inference import build_inference_record
from app.ai.models import (
    AIRequest, AIResponse, AITask, DataSensitivity,
    ReasoningMode, ResponseFormat,
)

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def test_build_inference_record_canonical_positional_api():
    request = AIRequest(
        task=AITask.RESEARCH,
        prompt="test",
        sensitivity=DataSensitivity.PUBLIC,
        reasoning_mode=ReasoningMode.REASONING,
        response_format=ResponseFormat.TEXT,
    )
    response = AIResponse(
        provider="TEST",
        model="test",
        content="ok",
        latency_ms=1.0,
        usage={},
    )
    record = build_inference_record(
        request,
        response,
        prompt_version="opportunity-research-v1.2",
        timestamp=NOW,
    )
    assert record.timestamp == NOW
    assert record.prompt_version == "opportunity-research-v1.2"
