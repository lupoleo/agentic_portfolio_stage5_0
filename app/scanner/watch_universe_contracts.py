"""Immutable contracts for Scanner candidate and portfolio watch assembly."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math

from app.scanner.history_quality_contracts import HistoryQualityResult
from app.scanner.instrument_eligibility import InstrumentEligibilityDecision
from app.scanner.market_data_contracts import (
    MarketDataVerification,
    SymbolMappingResult,
)
from app.scanner.universe_models import ListingKey, MarketListing


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonblank text")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class CandidateAssemblyStatus(str, Enum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"


class CandidateExclusionReason(str, Enum):
    INCLUDED = "INCLUDED"
    ELIGIBILITY_NOT_ELIGIBLE = "ELIGIBILITY_NOT_ELIGIBLE"
    ELIGIBILITY_REVIEW_REQUIRED = "ELIGIBILITY_REVIEW_REQUIRED"
    MAPPING_NOT_RESOLVED = "MAPPING_NOT_RESOLVED"
    VERIFICATION_NOT_READY = "VERIFICATION_NOT_READY"
    UPSTREAM_RESULT_MISSING = "UPSTREAM_RESULT_MISSING"
    HISTORY_BLOCKED = "HISTORY_BLOCKED"
    HISTORY_REVIEW_REQUIRED = "HISTORY_REVIEW_REQUIRED"
    LIQUIDITY_NOT_PASSING = "LIQUIDITY_NOT_PASSING"
    RECENT_LISTING_POLICY_NOT_MET = "RECENT_LISTING_POLICY_NOT_MET"


class PortfolioWatchStatus(str, Enum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"


class PortfolioWatchReason(str, Enum):
    CURRENT_POSITION = "CURRENT_POSITION"
    FLAT_POSITION = "FLAT_POSITION"


class WatchProvenance(str, Enum):
    NEW_CANDIDATE = "NEW_CANDIDATE"
    CURRENT_POSITION = "CURRENT_POSITION"


class ResearchReadiness(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class WatchUniversePolicy:
    policy_id: str = "scanner-v1-candidate-watch-assembly"
    policy_version: str = "1"
    market_data_provider_id: str = "yahoo"
    allow_recent_listing_short_liquidity: bool = True

    def __post_init__(self) -> None:
        for field in ("policy_id", "policy_version", "market_data_provider_id"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if type(self.allow_recent_listing_short_liquidity) is not bool:
            raise ValueError("allow_recent_listing_short_liquidity must be bool")


@dataclass(frozen=True)
class CandidateEvidenceBundle:
    listing: MarketListing
    eligibility: InstrumentEligibilityDecision
    mapping: SymbolMappingResult | None = None
    verification: MarketDataVerification | None = None
    history: HistoryQualityResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.listing, MarketListing):
            raise ValueError("listing must be a MarketListing")
        if not isinstance(self.eligibility, InstrumentEligibilityDecision):
            raise ValueError("eligibility must be an InstrumentEligibilityDecision")
        if self.eligibility.listing_key != self.listing.key:
            raise ValueError("eligibility belongs to another listing")
        if self.mapping is not None and not isinstance(self.mapping, SymbolMappingResult):
            raise ValueError("mapping must be a SymbolMappingResult")
        if self.verification is not None and not isinstance(
            self.verification, MarketDataVerification
        ):
            raise ValueError("verification must be a MarketDataVerification")
        if self.history is not None and not isinstance(self.history, HistoryQualityResult):
            raise ValueError("history must be a HistoryQualityResult")


@dataclass(frozen=True)
class CandidateAssemblyDecision:
    listing_key: ListingKey
    status: CandidateAssemblyStatus
    reason: CandidateExclusionReason
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.listing_key, ListingKey):
            raise ValueError("invalid listing_key")
        if not isinstance(self.status, CandidateAssemblyStatus):
            raise ValueError("invalid candidate status")
        if not isinstance(self.reason, CandidateExclusionReason):
            raise ValueError("invalid candidate reason")
        object.__setattr__(self, "message", _text(self.message, "message"))
        if (self.status is CandidateAssemblyStatus.INCLUDED) != (
            self.reason is CandidateExclusionReason.INCLUDED
        ):
            raise ValueError("included candidate must use INCLUDED reason")


@dataclass(frozen=True)
class ScannerCandidate:
    listing: MarketListing
    eligibility: InstrumentEligibilityDecision
    mapping: SymbolMappingResult
    verification: MarketDataVerification
    history: HistoryQualityResult

    def __post_init__(self) -> None:
        key = self.listing.key
        if any(
            value != key
            for value in (
                self.eligibility.listing_key,
                self.mapping.listing_key,
                self.verification.mapping.listing_key,
                self.history.listing_key,
            )
        ):
            raise ValueError("candidate evidence identities differ")


@dataclass(frozen=True)
class PortfolioPositionSnapshot:
    position_ref: str
    snapshot_id: str
    broker_id: str
    name: str
    isin: str | None
    broker_symbol: str
    market: str
    instrument_type: str
    currency: str
    quantity: float
    direction: str
    yahoo_symbol: str | None
    position_fingerprint: str

    def __post_init__(self) -> None:
        for field in (
            "position_ref",
            "snapshot_id",
            "broker_id",
            "name",
            "broker_symbol",
            "market",
            "instrument_type",
            "currency",
            "position_fingerprint",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(self, "isin", _optional_text(self.isin))
        object.__setattr__(self, "yahoo_symbol", _optional_text(self.yahoo_symbol))
        if type(self.quantity) not in (int, float) or not math.isfinite(self.quantity):
            raise ValueError("quantity must be finite")
        if self.direction not in {"LONG", "SHORT", "FLAT"}:
            raise ValueError("invalid direction")


@dataclass(frozen=True)
class PortfolioWatchDecision:
    position_ref: str
    status: PortfolioWatchStatus
    reason: PortfolioWatchReason
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_ref", _text(self.position_ref, "position_ref"))
        object.__setattr__(self, "message", _text(self.message, "message"))
        if not isinstance(self.status, PortfolioWatchStatus):
            raise ValueError("invalid portfolio watch status")
        if not isinstance(self.reason, PortfolioWatchReason):
            raise ValueError("invalid portfolio watch reason")
        if (self.status is PortfolioWatchStatus.INCLUDED) != (
            self.reason is PortfolioWatchReason.CURRENT_POSITION
        ):
            raise ValueError("included position must use CURRENT_POSITION reason")


@dataclass(frozen=True, order=True)
class ResearchSubjectKey:
    namespace: str
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "namespace", _text(self.namespace, "namespace").upper())
        object.__setattr__(self, "value", _text(self.value, "value").upper())
        if self.namespace not in {"YAHOO", "ISIN", "LISTING", "BROKER"}:
            raise ValueError("unsupported research subject namespace")


@dataclass(frozen=True)
class ResearchWatchMember:
    subject_key: ResearchSubjectKey
    provenances: tuple[WatchProvenance, ...]
    candidate_keys: tuple[ListingKey, ...]
    position_refs: tuple[str, ...]
    yahoo_symbols: tuple[str, ...]
    isins: tuple[str, ...]
    history_routes: tuple[str, ...]
    readiness: ResearchReadiness
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.subject_key, ResearchSubjectKey):
            raise ValueError("invalid subject key")
        provenances = tuple(sorted(set(self.provenances), key=lambda item: item.value))
        candidates = tuple(sorted(set(self.candidate_keys)))
        positions = tuple(sorted({_text(value, "position_ref") for value in self.position_refs}))
        yahoo = tuple(sorted({_text(value, "yahoo_symbol").upper() for value in self.yahoo_symbols}))
        isins = tuple(sorted({_text(value, "isin").upper() for value in self.isins}))
        routes = tuple(sorted({_text(value, "history_route") for value in self.history_routes}))
        diagnostics = tuple(sorted({_text(value, "diagnostic") for value in self.diagnostics}))
        if not provenances:
            raise ValueError("research member requires provenance")
        if not isinstance(self.readiness, ResearchReadiness):
            raise ValueError("invalid research readiness")
        object.__setattr__(self, "provenances", provenances)
        object.__setattr__(self, "candidate_keys", candidates)
        object.__setattr__(self, "position_refs", positions)
        object.__setattr__(self, "yahoo_symbols", yahoo)
        object.__setattr__(self, "isins", isins)
        object.__setattr__(self, "history_routes", routes)
        object.__setattr__(self, "diagnostics", diagnostics)


@dataclass(frozen=True)
class ResearchWatchUniverse:
    run_id: str
    assembled_at: datetime
    as_of: datetime
    portfolio_snapshot_id: str
    policy: WatchUniversePolicy
    source_fingerprints: tuple[tuple[str, str], ...]
    candidate_decisions: tuple[CandidateAssemblyDecision, ...]
    candidate_set: tuple[ScannerCandidate, ...]
    portfolio_decisions: tuple[PortfolioWatchDecision, ...]
    portfolio_watch_set: tuple[PortfolioPositionSnapshot, ...]
    members: tuple[ResearchWatchMember, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        for field in ("run_id", "portfolio_snapshot_id", "fingerprint"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        _aware(self.assembled_at, "assembled_at")
        _aware(self.as_of, "as_of")
        if self.assembled_at < self.as_of:
            raise ValueError("assembled_at cannot precede as_of")
        if not isinstance(self.policy, WatchUniversePolicy):
            raise ValueError("invalid watch-universe policy")
        sources = tuple(sorted(
            (_text(key, "source key"), _text(value, "source fingerprint"))
            for key, value in self.source_fingerprints
        ))
        if len({key for key, _ in sources}) != len(sources):
            raise ValueError("duplicate source fingerprint key")
        object.__setattr__(self, "source_fingerprints", sources)
