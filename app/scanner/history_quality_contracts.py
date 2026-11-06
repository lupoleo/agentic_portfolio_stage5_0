"""Immutable inputs and outcomes for Scanner history gates (no network)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
import math

from app.scanner.universe_models import ListingKey


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNDETERMINED = "UNDETERMINED"


class HistoryRoute(str, Enum):
    STANDARD = "STANDARD"
    RECENT_LISTING = "RECENT_LISTING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


def aware(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")


def day(value):
    if type(value) is not date:
        raise ValueError("Session label must be a date")


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nonblank provenance required")


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class DailyHistoryBar:
    session: date
    # Nominal provider OHLC; never multiply dividend-adjusted prices by volume.
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    adjusted_close: float | None = None

    def __post_init__(self):
        day(self.session)
        # Invalid numeric observations are retained for auditable gate failure.


@dataclass(frozen=True)
class HistorySnapshot:
    listing_key: ListingKey
    yahoo_symbol: str
    mapping_version: str
    fetched_at: datetime
    start_session: date
    end_session: date  # inclusive; pending sessions are filtered by calendar
    price_unit: str  # EUR, USD, CHF, GBp/GBX, ...
    source: str
    adjustment_basis: str
    bars: tuple[DailyHistoryBar, ...]

    def __post_init__(self):
        if not isinstance(self.listing_key, ListingKey):
            raise ValueError("Invalid listing key")
        aware(self.fetched_at)
        day(self.start_session)
        day(self.end_session)
        if self.start_session > self.end_session:
            raise ValueError("Invalid snapshot interval")
        for value in (self.yahoo_symbol, self.mapping_version, self.price_unit,
                      self.source, self.adjustment_basis):
            text(value)
        object.__setattr__(self, "bars", tuple(self.bars))
        if not all(isinstance(b, DailyHistoryBar) for b in self.bars):
            raise ValueError("Invalid history bar")


@dataclass(frozen=True)
class MarketSession:
    session: date
    closes_at: datetime

    def __post_init__(self):
        day(self.session)
        aware(self.closes_at)


@dataclass(frozen=True)
class SessionCalendar:
    exchange: str
    source: str
    version: str
    coverage_start: date
    coverage_end: date
    # Calendar provider guarantees a complete schedule over this date range.
    sessions: tuple[MarketSession, ...]

    def __post_init__(self):
        for value in (self.exchange, self.source, self.version):
            text(value)
        day(self.coverage_start)
        day(self.coverage_end)
        if self.coverage_start > self.coverage_end:
            raise ValueError("Invalid calendar coverage")
        sessions = tuple(self.sessions)
        if not all(isinstance(s, MarketSession) for s in sessions):
            raise ValueError("Invalid session")
        if len({s.session for s in sessions}) != len(sessions):
            raise ValueError("Duplicate calendar session")
        sessions = tuple(sorted(sessions, key=lambda s: s.session))
        if any(not self.coverage_start <= s.session <= self.coverage_end for s in sessions):
            raise ValueError("Calendar session outside declared coverage")
        if any(a.closes_at >= b.closes_at for a, b in zip(sessions, sessions[1:])):
            raise ValueError("Non-increasing session closes")
        object.__setattr__(self, "sessions", sessions)


@dataclass(frozen=True)
class ListingStartEvidence:
    listing_key: ListingKey
    first_session: date
    source: str
    known_at: datetime
    event_kind: str = "LISTING_START"  # IPO only with separate event evidence

    def __post_init__(self):
        if not isinstance(self.listing_key, ListingKey):
            raise ValueError("Invalid listing evidence key")
        day(self.first_session)
        aware(self.known_at)
        text(self.source)
        if self.event_kind not in {"LISTING_START", "IPO"}:
            raise ValueError("Unknown listing event kind")


@dataclass(frozen=True)
class SessionFXRate:
    session: date
    currency: str  # major currency, e.g. GBP rather than GBX
    eur_per_unit: float
    source: str
    known_at: datetime

    def __post_init__(self):
        day(self.session)
        aware(self.known_at)
        text(self.currency)
        text(self.source)
        if not positive(self.eur_per_unit):
            raise ValueError("FX rate must be finite and positive")


@dataclass(frozen=True)
class HistoryQualityPolicy:
    policy_id: str = "scanner-v1-history-quality"
    policy_version: str = "1"
    min_standard_sessions: int = 50
    coverage_window: int = 60
    minimum_coverage: float = .95
    liquidity_window: int = 20
    minimum_positive_volume_sessions: int = 19
    minimum_median_turnover_eur: float = 1_000_000.
    publication_grace: timedelta = timedelta(hours=1)

    def __post_init__(self):
        text(self.policy_id)
        text(self.policy_version)
        for value in (self.min_standard_sessions, self.coverage_window,
                      self.liquidity_window, self.minimum_positive_volume_sessions):
            if type(value) is not int or value < 1:
                raise ValueError("Positive integer policy windows required")
        if self.min_standard_sessions < 50:
            raise ValueError("Standard technical analysis requires 50 sessions")
        if not positive(self.minimum_coverage) or self.minimum_coverage > 1:
            raise ValueError("Invalid coverage threshold")
        if self.minimum_positive_volume_sessions > self.liquidity_window:
            raise ValueError("Positive-volume count exceeds window")
        if not positive(self.minimum_median_turnover_eur):
            raise ValueError("Invalid liquidity threshold")
        if not isinstance(self.publication_grace, timedelta) or self.publication_grace < timedelta(0):
            raise ValueError("Invalid publication grace")


@dataclass(frozen=True)
class HistoryGate:
    name: str
    status: GateStatus
    reason: str


@dataclass(frozen=True)
class IndicatorCapability:
    name: str
    available: bool
    required_sessions: int
    observed_sessions: int
    reason: str


@dataclass(frozen=True)
class HistoryQualityResult:
    listing_key: ListingKey
    yahoo_symbol: str
    as_of: datetime
    policy: HistoryQualityPolicy
    snapshot_fingerprint: str
    evidence_fingerprint: str
    evidence_sources: tuple[tuple[str, str], ...]
    route: HistoryRoute
    gates: tuple[HistoryGate, ...]
    indicators: tuple[IndicatorCapability, ...]
    metrics: tuple[tuple[str, object], ...]
    listing_event: str | None

    @property
    def standard_ready(self):
        return self.route is HistoryRoute.STANDARD
