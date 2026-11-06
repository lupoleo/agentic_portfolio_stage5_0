from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AIModel(BaseModel):
    """Base configuration for provider-neutral AI contracts."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
        populate_by_name=True,
    )


class AITask(str, Enum):
    SENTIMENT = "SENTIMENT"
    CLASSIFICATION = "CLASSIFICATION"
    EXTRACTION = "EXTRACTION"
    RELEVANCE = "RELEVANCE"
    TRIAGE = "TRIAGE"
    RESEARCH = "RESEARCH"
    REASONING = "REASONING"


class DataSensitivity(str, Enum):
    PUBLIC = "PUBLIC"
    PORTFOLIO = "PORTFOLIO"
    ACCOUNT = "ACCOUNT"


class ReasoningMode(str, Enum):
    FAST = "FAST"
    REASONING = "REASONING"


class ResponseFormat(str, Enum):
    TEXT = "TEXT"
    JSON = "JSON"


class AIRequest(AIModel):
    """Provider-neutral inference request.

    ``output_schema`` is a Pydantic model class used only for typed JSON
    responses. Providers may translate it to their native structured-output
    mechanism, but callers remain provider-neutral.
    """

    task: AITask
    prompt: str = Field(min_length=1)
    sensitivity: DataSensitivity = DataSensitivity.PUBLIC
    reasoning_mode: ReasoningMode = ReasoningMode.FAST
    response_format: ResponseFormat = ResponseFormat.TEXT
    output_schema: type[BaseModel] | None = Field(
        default=None,
        exclude=True,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_request(self) -> "AIRequest":
        if not self.prompt.strip():
            raise ValueError("prompt must contain non-whitespace text")

        if (
            self.output_schema is not None
            and self.response_format is not ResponseFormat.JSON
        ):
            raise ValueError(
                "output_schema requires response_format=JSON"
            )

        return self


class AIResponse(AIModel):
    """Provider-neutral result returned by an AIModelProvider."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    content: str = ""
    structured_output: dict[str, Any] | None = None
    latency_ms: float = Field(ge=0.0)
    usage: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result_payload(self) -> "AIResponse":
        if not self.content.strip() and self.structured_output is None:
            raise ValueError(
                "AIResponse requires content or structured_output"
            )
        return self
