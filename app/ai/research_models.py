from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.ai.models import AIModel


class ResearchStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceQuality(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExpectationsAssessment(str, Enum):
    NOT_PRICED_IN = "NOT_PRICED_IN"
    PARTIALLY_PRICED_IN = "PARTIALLY_PRICED_IN"
    LARGELY_PRICED_IN = "LARGELY_PRICED_IN"
    UNKNOWN = "UNKNOWN"


class OpportunityResearch(AIModel):
    """
    Structured research produced for one ScanCandidate.

    This object interprets evidence. It deliberately does not contain
    a trading decision, canonical direction, position size, broker
    instrument or CIO lifecycle state.
    """

    research_id: str
    candidate_id: str
    scan_id: str
    created_at: datetime

    ticker: str

    portfolio_snapshot_id: str | None = None
    risk_state_id: str | None = None

    research_status: ResearchStatus

    market_context: str | None = None
    fundamental_context: str | None = None
    technical_context: str | None = None
    event_context: str | None = None

    catalyst_assessment: str | None = None
    expectations_assessment: ExpectationsAssessment = (
        ExpectationsAssessment.UNKNOWN
    )

    bull_case: str | None = None
    bear_case: str | None = None

    key_risks: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    # AI-8C.2-R1: future outcomes no as-of evidence can establish. Unlike
    # unknowns (missing as-of facts), they do not by themselves block COMPLETE.
    forward_uncertainties: list[str] = Field(default_factory=list)

    evidence_quality: EvidenceQuality
    research_confidence: float = Field(ge=0.0, le=1.0)

    evidence_ids: list[str] = Field(default_factory=list)
    inference_ids: list[str] = Field(default_factory=list)

    requires_additional_research: bool = False

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        normalized = value.strip().upper()

        if not normalized:
            raise ValueError("ticker cannot be blank")

        return normalized

    @field_validator(
        "research_id",
        "candidate_id",
        "scan_id",
        mode="before",
    )
    @classmethod
    def reject_blank_ids(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        normalized = value.strip()

        if not normalized:
            raise ValueError("identifier cannot be blank")

        return normalized

    @field_validator(
        "key_risks",
        "contradictory_evidence",
        "unknowns",
        "forward_uncertainties",
        "evidence_ids",
        "inference_ids",
    )
    @classmethod
    def reject_blank_list_items(
        cls,
        values: list[str],
    ) -> list[str]:
        for value in values:
            if not value.strip():
                raise ValueError(
                    "list items cannot be blank"
                )
        return values

    @model_validator(mode="after")
    def validate_research_state(self):
        if (
            self.research_status
            == ResearchStatus.INSUFFICIENT_EVIDENCE
            and not self.requires_additional_research
        ):
            raise ValueError(
                "INSUFFICIENT_EVIDENCE requires "
                "requires_additional_research=True"
            )

        if (
            self.research_status
            == ResearchStatus.COMPLETE
            and self.requires_additional_research
        ):
            raise ValueError(
                "COMPLETE research cannot require "
                "additional research"
            )

        return self
