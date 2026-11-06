from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .models import AIModel


class ScannerType(str, Enum):
    PORTFOLIO = "PORTFOLIO"
    MARKET = "MARKET"
    EVENT = "EVENT"
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    NEWS = "NEWS"
    HYBRID = "HYBRID"


class ScanUniverseType(str, Enum):
    PORTFOLIO = "PORTFOLIO"
    WATCHLIST = "WATCHLIST"
    INDEX = "INDEX"
    SECTOR = "SECTOR"
    CUSTOM = "CUSTOM"
    MARKET = "MARKET"


class MarketScanStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CandidateOrigin(str, Enum):
    PORTFOLIO = "PORTFOLIO"
    EXTERNAL = "EXTERNAL"


class CandidateAction(str, Enum):
    ADD = "ADD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    HEDGE = "HEDGE"
    REVERSE = "REVERSE"
    NO_ACTION = "NO_ACTION"
    NEW_LONG = "NEW_LONG"
    NEW_SHORT = "NEW_SHORT"


class SignalType(str, Enum):
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    EVENT = "EVENT"
    NEWS = "NEWS"
    ANALYST_REVISION = "ANALYST_REVISION"
    MOMENTUM = "MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"
    RISK = "RISK"
    MACRO = "MACRO"
    MULTI_FACTOR = "MULTI_FACTOR"


class CatalystType(str, Enum):
    EARNINGS = "EARNINGS"
    GUIDANCE = "GUIDANCE"
    ANALYST_RATING = "ANALYST_RATING"
    INVESTOR_DAY = "INVESTOR_DAY"
    PRODUCT = "PRODUCT"
    FDA_CLINICAL = "FDA_CLINICAL"
    M_AND_A = "M_AND_A"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    MACRO = "MACRO"
    OTHER = "OTHER"


class MarketScan(AIModel):
    scan_id: str = Field(min_length=1)
    created_at: datetime

    scanner_type: ScannerType
    scanner_version: str = Field(min_length=1)

    universe_type: ScanUniverseType
    universe_name: str | None = None

    portfolio_snapshot_id: str | None = None
    risk_state_id: str | None = None

    symbols_requested: list[str] = Field(default_factory=list)
    symbols_scanned: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)

    status: MarketScanStatus

    started_at: datetime
    completed_at: datetime | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "MarketScan":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be earlier than started_at")

        if self.status == MarketScanStatus.RUNNING and self.completed_at is not None:
            raise ValueError("RUNNING scan cannot have completed_at")

        if self.status in {MarketScanStatus.COMPLETED, MarketScanStatus.FAILED}:
            if self.completed_at is None:
                raise ValueError("terminal scan status requires completed_at")

        return self


class ScanCandidate(AIModel):
    candidate_id: str = Field(min_length=1)
    scan_id: str = Field(min_length=1)
    created_at: datetime

    ticker: str = Field(min_length=1)

    origin: CandidateOrigin
    action: CandidateAction
    signal_type: SignalType

    raw_score: float | None = None
    scanner_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    thesis_summary: str | None = None

    catalyst_type: CatalystType | None = None
    catalyst_datetime: datetime | None = None

    evidence_ids: list[str] = Field(default_factory=list)
    inference_ids: list[str] = Field(default_factory=list)

    portfolio_snapshot_id: str | None = None
    risk_state_id: str | None = None

    requires_research: bool = True

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        if not isinstance(value, str):
            return value

        normalized = value.strip().upper()

        if not normalized:
            raise ValueError("ticker cannot be blank")

        return normalized

    @model_validator(mode="after")
    def validate_origin_action(self) -> "ScanCandidate":
        portfolio_actions = {
            CandidateAction.ADD,
            CandidateAction.REDUCE,
            CandidateAction.EXIT,
            CandidateAction.HEDGE,
            CandidateAction.REVERSE,
            CandidateAction.NO_ACTION,
        }
        external_actions = {
            CandidateAction.NEW_LONG,
            CandidateAction.NEW_SHORT,
        }

        if self.origin == CandidateOrigin.PORTFOLIO and self.action not in portfolio_actions:
            raise ValueError(
                "PORTFOLIO candidate requires an existing-position action"
            )

        if self.origin == CandidateOrigin.EXTERNAL and self.action not in external_actions:
            raise ValueError(
                "EXTERNAL candidate requires NEW_LONG or NEW_SHORT"
            )

        return self
