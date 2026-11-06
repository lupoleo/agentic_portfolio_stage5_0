"""Contracts for E2E-S4.0A.1 bounded candidate replenishment."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.e2e.stage4_contracts import (
    Stage4Mode,
    Stage4Model,
    canonical_fingerprint,
)


class ReplenishmentDisposition(str, Enum):
    RETRY_CURRENT = "RETRY_CURRENT"
    ADVANCE_FRONTIER = "ADVANCE_FRONTIER"
    STOP_FAIL_CLOSED = "STOP_FAIL_CLOSED"


class ReplenishmentStopReason(str, Enum):
    NEXT_WAVE_READY = "NEXT_WAVE_READY"
    RETRY_CURRENT_READY = "RETRY_CURRENT_READY"
    SELECTABLE_OPPORTUNITY_FOUND = "SELECTABLE_OPPORTUNITY_FOUND"
    PENDING_DIRECTIONAL_WORK = "PENDING_DIRECTIONAL_WORK"
    UNIVERSE_EXHAUSTED = "UNIVERSE_EXHAUSTED"
    REPLENISHMENT_BUDGET_EXHAUSTED = "REPLENISHMENT_BUDGET_EXHAUSTED"
    SAFETY_BLOCK = "SAFETY_BLOCK"


class CandidateWaveStatus(str, Enum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


def _nonblank(value: Any, field: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{field} must be nonblank")
    return result


def normalize_listing_key(value: str) -> str:
    raw = _nonblank(value, "listing_key").upper()
    exchange, separator, symbol = raw.partition(":")
    if not separator or not exchange.strip() or not symbol.strip():
        raise ValueError("listing_key must use EXCHANGE:SYMBOL")
    return f"{exchange.strip()}:{symbol.strip()}"


class CandidateListingRef(Stage4Model):
    listing_key: str
    exchange: str
    symbol: str
    yahoo_symbol: str

    @model_validator(mode="after")
    def validate_identity(self):
        exchange = _nonblank(self.exchange, "exchange").upper()
        symbol = _nonblank(self.symbol, "symbol").upper()
        yahoo = _nonblank(self.yahoo_symbol, "yahoo_symbol").upper()
        key = normalize_listing_key(self.listing_key)
        if key != f"{exchange}:{symbol}":
            raise ValueError("listing_key differs from exchange and symbol")
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "yahoo_symbol", yahoo)
        object.__setattr__(self, "listing_key", key)
        return self


class FrontierRankingPolicy(Stage4Model):
    policy_id: str = "stage4-news-sensitive-frontier"
    policy_version: str = "1"
    target_size: int = Field(default=48, ge=2, le=200)
    max_iterations: int = Field(default=10, ge=1, le=20)
    news_lookback_days: int = Field(default=5, ge=1, le=30)
    max_news_items: int = Field(default=5, ge=1, le=20)
    max_workers: int = Field(default=4, ge=1, le=8)
    max_provider_retries: int = Field(default=1, ge=0, le=2)

    @field_validator("policy_id", "policy_version", mode="before")
    @classmethod
    def identity_fields(cls, value):
        return _nonblank(value, "frontier ranking policy identity")


class CandidateReplenishmentPolicy(Stage4Model):
    policy_id: str = "stage4-v2-news-sensitive-replenishment"
    policy_version: str = "2"
    listing_batch_size: int = Field(default=2, ge=1, le=3)
    max_waves: int = Field(default=5, ge=1, le=20)
    max_transient_retries: int = Field(default=1, ge=0, le=3)
    supported_venues: tuple[str, ...] = ("BIT", "NASDAQ", "NYSE", "XETRA")
    frontier_ranking: FrontierRankingPolicy = Field(
        default_factory=FrontierRankingPolicy
    )
    preserve_long_short_symmetry: bool = True
    allow_threshold_relaxation: bool = False
    allow_execution: bool = False

    @field_validator("policy_id", "policy_version", mode="before")
    @classmethod
    def text_fields(cls, value):
        return _nonblank(value, "policy identity")

    @model_validator(mode="after")
    def validate_policy(self):
        venues = tuple(sorted({
            _nonblank(value, "supported venue").upper()
            for value in self.supported_venues
        }))
        if not venues:
            raise ValueError("at least one supported venue is required")
        object.__setattr__(self, "supported_venues", venues)
        if not self.preserve_long_short_symmetry:
            raise ValueError("candidate replenishment requires LONG/SHORT symmetry")
        if self.allow_threshold_relaxation:
            raise ValueError("candidate replenishment cannot relax quality thresholds")
        if self.allow_execution:
            raise ValueError("candidate replenishment cannot enable execution")
        return self


class CandidateReplenishmentSession(Stage4Model):
    session_id: str
    root_run_id: str
    portfolio_snapshot_id: str
    portfolio_risk_state_id: str
    as_of: datetime
    mode: Stage4Mode
    policy: CandidateReplenishmentPolicy = Field(
        default_factory=CandidateReplenishmentPolicy
    )
    fingerprint: str

    @field_validator(
        "session_id", "root_run_id", "portfolio_snapshot_id",
        "portfolio_risk_state_id", "fingerprint", mode="before",
    )
    @classmethod
    def identity_fields(cls, value):
        return _nonblank(value, "session identity")

    @model_validator(mode="after")
    def validate_session(self):
        if self.as_of.utcoffset() is None:
            raise ValueError("session as_of must be timezone-aware")
        expected = candidate_replenishment_session_fingerprint(
            root_run_id=self.root_run_id,
            portfolio_snapshot_id=self.portfolio_snapshot_id,
            portfolio_risk_state_id=self.portfolio_risk_state_id,
            as_of=self.as_of,
            mode=self.mode,
            policy=self.policy,
        )
        if self.fingerprint != expected:
            raise ValueError("candidate replenishment session fingerprint differs")
        if self.session_id != "s4a1-" + expected[:24]:
            raise ValueError("candidate replenishment session_id differs")
        return self


class CandidateWavePlan(Stage4Model):
    session_id: str
    wave_index: int = Field(ge=1)
    disposition: ReplenishmentDisposition
    reason: ReplenishmentStopReason
    selected_listings: tuple[CandidateListingRef, ...] = ()
    attempted_listing_keys: tuple[str, ...] = ()
    retry_count: int = Field(default=0, ge=0)
    diagnostics: tuple[str, ...] = ()
    fingerprint: str

    @model_validator(mode="after")
    def validate_plan(self):
        session_id = _nonblank(self.session_id, "session_id")
        attempted = tuple(sorted({
            normalize_listing_key(value)
            for value in self.attempted_listing_keys
        }))
        selected = tuple(sorted(
            self.selected_listings,
            key=lambda value: value.listing_key,
        ))
        if len({value.listing_key for value in selected}) != len(selected):
            raise ValueError("wave plan contains duplicate listing keys")
        if self.disposition is ReplenishmentDisposition.ADVANCE_FRONTIER:
            if self.reason is not ReplenishmentStopReason.NEXT_WAVE_READY:
                raise ValueError("advance frontier requires NEXT_WAVE_READY")
            if not selected:
                raise ValueError("advance frontier requires selected listings")
        elif self.disposition is ReplenishmentDisposition.RETRY_CURRENT:
            if self.reason is not ReplenishmentStopReason.RETRY_CURRENT_READY:
                raise ValueError("retry requires RETRY_CURRENT_READY")
            if not selected:
                raise ValueError("retry requires current listings")
        elif selected:
            raise ValueError("fail-closed plan cannot select listings")
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "attempted_listing_keys", attempted)
        object.__setattr__(self, "selected_listings", selected)
        expected = candidate_wave_plan_fingerprint(self, exclude_fingerprint=True)
        if self.fingerprint != expected:
            raise ValueError("candidate wave plan fingerprint differs")
        return self


class CandidateWaveRecord(Stage4Model):
    record_id: str
    session_id: str
    wave_index: int = Field(ge=1)
    status: CandidateWaveStatus
    started_at: datetime
    completed_at: datetime | None = None
    plan_fingerprint: str
    listing_keys: tuple[str, ...]
    child_run_id: str | None = None
    scanner_run_id: str | None = None
    watch_universe_run_id: str | None = None
    research_run_id: str | None = None
    opportunity_ids: tuple[str, ...] = ()
    terminal_reason: str | None = None
    diagnostics: tuple[str, ...] = ()
    broker_orders_submitted: int = 0
    portfolio_mutations: int = 0
    automatic_executions: int = 0
    fingerprint: str

    @model_validator(mode="after")
    def validate_record(self):
        if self.started_at.utcoffset() is None:
            raise ValueError("wave started_at must be timezone-aware")
        if self.completed_at is not None:
            if self.completed_at.utcoffset() is None:
                raise ValueError("wave completed_at must be timezone-aware")
            if self.completed_at < self.started_at:
                raise ValueError("wave completed_at cannot precede started_at")
        if self.status in {
            CandidateWaveStatus.PARTIAL,
            CandidateWaveStatus.COMPLETED,
            CandidateWaveStatus.BLOCKED,
        } and self.completed_at is None:
            raise ValueError("terminal wave record requires completed_at")
        keys = tuple(sorted({normalize_listing_key(v) for v in self.listing_keys}))
        object.__setattr__(self, "listing_keys", keys)
        if any((
            self.broker_orders_submitted,
            self.portfolio_mutations,
            self.automatic_executions,
        )):
            raise ValueError("candidate replenishment cannot record side effects")
        expected = candidate_wave_record_fingerprint(self, exclude_fingerprint=True)
        if self.fingerprint != expected:
            raise ValueError("candidate wave record fingerprint differs")
        if self.record_id != "wave-" + expected[:24]:
            raise ValueError("candidate wave record_id differs")
        return self


def candidate_replenishment_session_fingerprint(
    *, root_run_id, portfolio_snapshot_id, portfolio_risk_state_id,
    as_of, mode, policy,
) -> str:
    return canonical_fingerprint({
        "root_run_id": root_run_id,
        "portfolio_snapshot_id": portfolio_snapshot_id,
        "portfolio_risk_state_id": portfolio_risk_state_id,
        "as_of": as_of.isoformat(),
        "mode": mode.value if isinstance(mode, Stage4Mode) else str(mode),
        "policy": policy.model_dump(mode="json"),
    })


def candidate_wave_plan_fingerprint(
    value: CandidateWavePlan | dict[str, Any], *, exclude_fingerprint=False,
) -> str:
    payload = _json_safe(value)
    if exclude_fingerprint:
        payload.pop("fingerprint", None)
    return canonical_fingerprint(payload)


def candidate_wave_record_fingerprint(
    value: CandidateWaveRecord | dict[str, Any], *, exclude_fingerprint=False,
) -> str:
    payload = _json_safe(value)
    defaults = {
        "completed_at": None,
        "child_run_id": None,
        "scanner_run_id": None,
        "watch_universe_run_id": None,
        "research_run_id": None,
        "opportunity_ids": [],
        "terminal_reason": None,
        "diagnostics": [],
        "broker_orders_submitted": 0,
        "portfolio_mutations": 0,
        "automatic_executions": 0,
    }
    payload = {**defaults, **payload}
    payload.pop("record_id", None)
    payload.pop("fingerprint", None)
    return canonical_fingerprint(payload)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value
