from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from app.ai.models import AIModel
from app.ai.research_service import ResearchEvidence


class EvidenceKind(str, Enum):
    MARKET = "MARKET"
    FUNDAMENTAL = "FUNDAMENTAL"
    TECHNICAL = "TECHNICAL"
    NEWS = "NEWS"
    EVENT = "EVENT"
    ANALYST = "ANALYST"
    MACRO = "MACRO"
    OTHER = "OTHER"


class EvidenceFetchStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NO_DATA = "NO_DATA"


class EvidenceRequest(AIModel):
    ticker: str
    as_of: datetime | None = None
    kinds: list[EvidenceKind] = Field(default_factory=list)
    max_items: int = Field(default=20, ge=1, le=200)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("ticker must be a non-empty string")
        return value.strip().upper()


class EvidenceSource(AIModel):
    source_id: str
    provider: str
    source_type: str
    source_name: str
    source_url: str | None = None
    retrieved_at: datetime
    published_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "source_id", "provider", "source_type", "source_name",
        mode="before",
    )
    @classmethod
    def reject_blank_strings(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("source identity fields must be non-empty")
        return value.strip()


class EvidenceItem(AIModel):
    evidence: ResearchEvidence
    source: EvidenceSource
    kind: EvidenceKind
    ticker: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("ticker must be a non-empty string")
        return value.strip().upper()


class EvidenceFetchResult(AIModel):
    provider: str
    ticker: str
    status: EvidenceFetchStatus
    items: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fetched_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider", mode="before")
    @classmethod
    def reject_blank_provider(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("provider must be non-empty")
        return value.strip()

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("ticker must be a non-empty string")
        return value.strip().upper()


class EvidenceProviderError(RuntimeError):
    """Base error for evidence-provider failures."""


class EvidenceProviderConnectionError(EvidenceProviderError):
    """The upstream evidence source could not be reached."""


class EvidenceProviderTimeoutError(EvidenceProviderError):
    """The upstream evidence source timed out."""


class EvidenceProviderResponseError(EvidenceProviderError):
    """The upstream source returned an invalid/unusable response."""


class EvidenceProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        raise NotImplementedError
