"""Scanner market-data boundary. No network or broker dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.scanner.universe_models import ListingKey


class SymbolMappingStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNMAPPED = "UNMAPPED"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID_INPUT = "INVALID_INPUT"


class MarketDataIdentityStatus(str, Enum):
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    UNVERIFIED = "UNVERIFIED"


class MarketDataAvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    NO_DATA = "NO_DATA"
    NOT_FOUND = "NOT_FOUND"
    TEMPORARY_ERROR = "TEMPORARY_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NOT_CHECKED = "NOT_CHECKED"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonblank text")
    return value.strip()


def _aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True)
class MarketDataDiagnostic:
    code: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _text(self.code, "code"))
        object.__setattr__(self, "message", _text(self.message, "message"))


@dataclass(frozen=True)
class SymbolMappingResult:
    listing_key: ListingKey
    provider_id: str
    mapping_version: str
    status: SymbolMappingStatus
    candidate_symbols: tuple[str, ...] = ()
    rule_id: str | None = None
    diagnostics: tuple[MarketDataDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.listing_key, ListingKey):
            raise ValueError("listing_key must be a ListingKey")
        if not isinstance(self.status, SymbolMappingStatus):
            raise ValueError("status must be a SymbolMappingStatus")
        for field in ("provider_id", "mapping_version"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        candidates = tuple(sorted(set(
            _text(symbol, "candidate_symbol") for symbol in self.candidate_symbols
        )))
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(d, MarketDataDiagnostic) for d in diagnostics):
            raise ValueError("invalid diagnostic")
        if self.status is SymbolMappingStatus.RESOLVED:
            if len(candidates) != 1:
                raise ValueError("RESOLVED requires exactly one candidate")
            _text(self.rule_id, "rule_id")
        elif self.status is SymbolMappingStatus.AMBIGUOUS:
            if len(candidates) < 2:
                raise ValueError("AMBIGUOUS requires multiple candidates")
        elif candidates:
            raise ValueError("unresolved mapping must not contain candidates")
        if self.status is not SymbolMappingStatus.RESOLVED and not diagnostics:
            raise ValueError("unresolved mapping requires diagnostics")
        object.__setattr__(self, "candidate_symbols", candidates)
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def resolved_symbol(self) -> str | None:
        if self.status is SymbolMappingStatus.RESOLVED:
            return self.candidate_symbols[0]
        return None


@dataclass(frozen=True)
class MarketDataVerification:
    """Evidence for one mapping; freshness is evaluated at an explicit time.

    Evidence pairs retain provider values (including currency units). A later
    adapter owns identity verification; a successful price fetch alone cannot
    set VERIFIED. This result does not encode Scanner eligibility.
    """

    mapping: SymbolMappingResult
    identity_status: MarketDataIdentityStatus
    availability_status: MarketDataAvailabilityStatus
    checked_at: datetime
    expires_at: datetime
    requested_start: datetime
    requested_end: datetime
    verification_version: str
    valid_bar_count: int = 0
    latest_bar_at: datetime | None = None
    identity_evidence: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[MarketDataDiagnostic, ...] = ()
    from_cache: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, SymbolMappingResult):
            raise ValueError("invalid mapping")
        if not isinstance(self.identity_status, MarketDataIdentityStatus):
            raise ValueError("invalid identity status")
        if not isinstance(self.availability_status, MarketDataAvailabilityStatus):
            raise ValueError("invalid availability status")
        for field in ("checked_at", "expires_at", "requested_start", "requested_end"):
            _aware(getattr(self, field), field)
        if self.expires_at <= self.checked_at:
            raise ValueError("expiry must follow verification")
        if self.requested_start >= self.requested_end:
            raise ValueError("invalid request interval")
        if type(self.valid_bar_count) is not int or self.valid_bar_count < 0:
            raise ValueError("invalid bar count")
        if type(self.from_cache) is not bool:
            raise ValueError("from_cache must be bool")
        object.__setattr__(self, "verification_version", _text(
            self.verification_version, "verification_version"
        ))
        evidence = tuple(
            (_text(k, "evidence key"), _text(v, "evidence value"))
            for k, v in self.identity_evidence
        )
        if len({k for k, _ in evidence}) != len(evidence):
            raise ValueError("duplicate evidence keys")
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(d, MarketDataDiagnostic) for d in diagnostics):
            raise ValueError("invalid diagnostic")
        object.__setattr__(self, "identity_evidence", evidence)
        object.__setattr__(self, "diagnostics", diagnostics)
        if self.identity_status is MarketDataIdentityStatus.VERIFIED and not evidence:
            raise ValueError("VERIFIED requires evidence")
        if self.mapping.status is not SymbolMappingStatus.RESOLVED:
            if (self.identity_status is not MarketDataIdentityStatus.UNVERIFIED
                    or self.availability_status is not MarketDataAvailabilityStatus.NOT_CHECKED):
                raise ValueError("unresolved mapping cannot be verified or fetched")
        if self.availability_status is MarketDataAvailabilityStatus.AVAILABLE:
            if not self.valid_bar_count or self.latest_bar_at is None:
                raise ValueError("AVAILABLE requires valid bars and timestamp")
            _aware(self.latest_bar_at, "latest_bar_at")
            if not self.requested_start <= self.latest_bar_at < self.requested_end:
                raise ValueError("latest bar outside requested interval")
            if self.latest_bar_at > self.checked_at:
                raise ValueError("latest bar cannot be in the future")
        elif self.valid_bar_count or self.latest_bar_at is not None:
            raise ValueError("unavailable result cannot claim valid bars")
        if (self.availability_status not in {
                MarketDataAvailabilityStatus.AVAILABLE,
                MarketDataAvailabilityStatus.NOT_CHECKED,
            } or self.identity_status is MarketDataIdentityStatus.MISMATCH):
            if not diagnostics:
                raise ValueError("negative verification requires diagnostics")

    def ready_for_market_data(self, *, as_of: datetime) -> bool:
        """Market-data gate only; caller must separately enforce eligibility."""
        _aware(as_of, "as_of")
        return (
            self.mapping.status is SymbolMappingStatus.RESOLVED
            and self.identity_status is MarketDataIdentityStatus.VERIFIED
            and self.availability_status is MarketDataAvailabilityStatus.AVAILABLE
            and self.checked_at <= as_of < self.expires_at
        )
