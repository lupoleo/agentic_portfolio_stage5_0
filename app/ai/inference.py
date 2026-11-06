from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import Field

from .models import (
    AIModel,
    AIRequest,
    AIResponse,
    AITask,
    DataSensitivity,
    ReasoningMode,
    ResponseFormat,
)


class AIValidationStatus(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    VALIDATED = "VALIDATED"


class AIInferenceRecord(AIModel):
    """Immutable audit record for one successful AI inference."""

    inference_id: str = Field(min_length=1)
    timestamp: datetime

    task: AITask
    sensitivity: DataSensitivity
    reasoning_mode: ReasoningMode
    response_format: ResponseFormat

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)

    prompt_version: str | None = None
    prompt_sha256: str = Field(min_length=64, max_length=64)

    evidence_ids: list[str] = Field(default_factory=list)
    portfolio_snapshot_id: str | None = None
    risk_state_id: str | None = None

    request_metadata: dict[str, Any] = Field(default_factory=dict)

    raw_output: str
    validated_output: dict[str, Any] | None = None
    validation_status: AIValidationStatus

    latency_ms: float = Field(ge=0.0)
    usage: dict[str, int] = Field(default_factory=dict)


def build_inference_record(
    ai_request: AIRequest,
    ai_response: AIResponse,
    *,
    prompt_version: str | None = None,
    evidence_ids: list[str] | None = None,
    portfolio_snapshot_id: str | None = None,
    risk_state_id: str | None = None,
    timestamp: datetime | None = None,
    inference_id: str | None = None,
) -> AIInferenceRecord:
    """Build the canonical audit record for a successful inference."""

    resolved_timestamp = timestamp or datetime.now(timezone.utc)
    resolved_inference_id = inference_id or (
        f"AI-{resolved_timestamp.strftime('%Y%m%d-%H%M%S')}-"
        f"{uuid4().hex[:6]}"
    )

    prompt_hash = hashlib.sha256(
        ai_request.prompt.encode("utf-8")
    ).hexdigest()

    validation_status = AIValidationStatus.NOT_APPLICABLE
    validated_output = None

    if ai_request.output_schema is not None:
        validation_status = AIValidationStatus.VALIDATED
        validated_output = ai_response.structured_output

    return AIInferenceRecord(
        inference_id=resolved_inference_id,
        timestamp=resolved_timestamp,
        task=ai_request.task,
        sensitivity=ai_request.sensitivity,
        reasoning_mode=ai_request.reasoning_mode,
        response_format=ai_request.response_format,
        provider=ai_response.provider,
        model=ai_response.model,
        prompt_version=prompt_version,
        prompt_sha256=prompt_hash,
        evidence_ids=list(evidence_ids or []),
        portfolio_snapshot_id=portfolio_snapshot_id,
        risk_state_id=risk_state_id,
        request_metadata=dict(ai_request.metadata),
        raw_output=ai_response.content,
        validated_output=validated_output,
        validation_status=validation_status,
        latency_ms=ai_response.latency_ms,
        usage=dict(ai_response.usage),
    )
