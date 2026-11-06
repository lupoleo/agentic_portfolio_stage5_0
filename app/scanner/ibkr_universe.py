"""Stage 5 scanner universe from IB Gateway market scanners (S5.0-5, gaps G2, G4, G6).

4.0 acquired ~31.8k listings from EODHD, mapped each one to a Yahoo symbol
and downloaded Yahoo history. Here:

* the universe is a fixed, versioned **query plan** of IB Gateway scanners
  (location x scan code, at most 50 rows each); every run records the plan,
  the rows and the errors, so the universe is small but auditable (G2);
* identity is the IBKR conid: no symbol mapping is needed. The 4.0 mapping
  and verification records are still produced, with provider ``ibkr``, so
  every downstream contract stays unchanged;
* history comes from IB Gateway daily bars (completed sessions only, G4) and
  FX for the liquidity gate from IBKR ``EUR.<ccy>`` mid bars, exact session
  date and no forward fill (G6);
* the 4.0 eligibility rule (COMMON_STOCK only) and all history-quality gates
  run unchanged.

The output is the three 4.0 reports (S2.2B eligibility, S2.2C mapping,
S2.2D history) consumed by the watch-universe assembly, plus the
``InstrumentRef`` of every candidate for the run's instrument directory.
All calls are read-only data requests.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import json
import math

from app.broker.gateway import BrokerPermissionError, DailyBar, GatewaySession, ScannerRow
from app.broker.ibkr_client import BrokerClientError, BrokerNoDataError
from app.broker.instrument_directory import InstrumentRef
from app.scanner.exchange_session_calendar import CALENDAR_BINDINGS, ExchangeSessionCalendarProvider
from app.scanner.history_quality import evaluate_history_quality, history_quality_result_to_dict
from app.scanner.history_quality_contracts import (
    DailyHistoryBar,
    HistorySnapshot,
    SessionCalendar,
    SessionFXRate,
)
from app.scanner.instrument_eligibility import InstrumentEligibilityStatus, evaluate_instrument_eligibility
from app.scanner.market_data_contracts import (
    MarketDataAvailabilityStatus,
    MarketDataDiagnostic,
    MarketDataIdentityStatus,
    MarketDataVerification,
    SymbolMappingResult,
    SymbolMappingStatus,
)
from app.scanner.market_data_verification_cache import encode
from app.scanner.universe_models import MarketListing

PROVIDER_ID = "ibkr"
MAPPING_VERSION = "ibkr-conid-v1"
MAPPING_RULE = "ibkr-conid-native"
VERIFICATION_VERSION = "ibkr-gateway-verification-v1"
HISTORY_SOURCE = "ib-gateway:TRADES:1day"
ADJUSTMENT_BASIS = "ibkr-trades-split-adjusted"
FX_SOURCE = "ib-gateway:IDEALPRO:EUR.<ccy>:MIDPOINT"
US_EXCHANGES = frozenset({"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"})
# Research symbol = IBKR symbol plus the Yahoo suffix of the venue, so it is
# unique across venues and still works for the declared Yahoo secondaries.
RESEARCH_SUFFIX = {"IBIS": ".DE", "SBF": ".PA", "AEB": ".AS", "BVME": ".MI", "LSE": ".L"}
# IBKR ContractDetails.stockType -> raw type understood by the 4.0 taxonomy.
STOCK_TYPE_MAP = {
    "COMMON": "Common Stock",
    "ADR": "Common Stock",
    "GDR": "Common Stock",
    "NY REG SHRS": "Common Stock",
    "REIT": "Common Stock",
    "PREFERRED": "Preferred Stock",
    "ETF": "ETF",
    "ETN": "Note",
    "ETC": "ETC",
    "CLOSED-END FUND": "Fund",
    "FUND": "Fund",
    "RIGHT": "Right",
    "WARRANT": "Warrant",
    "UNIT": "Unit",
}


@dataclass(frozen=True)
class ScanQuery:
    instrument: str
    location: str
    scan_code: str
    rows: int = 50
    market_cap_above_musd: float | None = 2000.0


@dataclass(frozen=True)
class ScanPlan:
    plan_id: str
    version: str
    queries: tuple[ScanQuery, ...]

    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def _plan_queries(locations: Sequence[str], scan_codes: Sequence[str]) -> tuple[ScanQuery, ...]:
    return tuple(
        # European stock scans need the "STOCK.EU" instrument type; "STK" is US only.
        ScanQuery("STOCK.EU" if location.startswith("STK.EU.") else "STK", location, code)
        for location in locations
        for code in scan_codes
    )


# Venues verified with the S5.0-5 spikes on 2026-11-06: scanner rows and
# daily history permitted with no extra market-data subscription.
DEFAULT_LOCATIONS = ("STK.US.MAJOR", "STK.EU.IBIS", "STK.EU.SBF", "STK.EU.AEB", "STK.EU.BVME", "STK.EU.LSE")
DEFAULT_SCAN_CODES = ("MOST_ACTIVE", "TOP_PERC_GAIN", "HOT_BY_VOLUME")
DEFAULT_PLAN = ScanPlan("ibkr-scanner-universe", "1", _plan_queries(DEFAULT_LOCATIONS, DEFAULT_SCAN_CODES))


@dataclass(frozen=True)
class ScanOutcome:
    query: ScanQuery
    rows: tuple[ScannerRow, ...]
    error: str | None = None


@dataclass(frozen=True)
class UniverseListing:
    conid: int
    symbol: str
    exchange: str
    currency: str
    best_rank: int
    sources: tuple[str, ...]  # "<location>:<scan_code>" that returned it


@dataclass(frozen=True)
class UniverseAcquisition:
    plan: ScanPlan
    acquired_at: datetime
    scans: tuple[ScanOutcome, ...]
    listings: tuple[UniverseListing, ...]  # deduplicated by conid, best rank first


def acquire_universe(session: GatewaySession, plan: ScanPlan = DEFAULT_PLAN, *,
                     now: datetime | None = None) -> UniverseAcquisition:
    """Run every query of the plan; a failed scan is recorded, never fatal."""

    acquired_at = now or datetime.now(timezone.utc)
    outcomes = []
    by_conid: dict[int, dict] = {}
    for query in plan.queries:
        try:
            rows = tuple(session.scanner(query.instrument, query.location, query.scan_code, rows=query.rows,
                                         market_cap_above_musd=query.market_cap_above_musd))
            error = None
        except (BrokerClientError, BrokerNoDataError) as exc:
            rows, error = (), f"{type(exc).__name__}: {exc}"
        outcomes.append(ScanOutcome(query, rows, error))
        for row in rows:
            entry = by_conid.setdefault(row.conid, {"row": row, "rank": row.rank, "sources": set()})
            entry["rank"] = min(entry["rank"], row.rank)
            entry["sources"].add(f"{query.location}:{query.scan_code}")
    listings = sorted(
        (UniverseListing(conid, e["row"].symbol, e["row"].exchange.upper(), e["row"].currency.upper(), e["rank"],
                         tuple(sorted(e["sources"])))
         for conid, e in by_conid.items()),
        key=lambda item: (item.best_rank, -len(item.sources), item.conid),
    )
    return UniverseAcquisition(plan, acquired_at, tuple(outcomes), tuple(listings))


def research_symbol(symbol: str, exchange: str) -> str:
    symbol = symbol.strip().upper().replace(" ", "-")
    if exchange in US_EXCHANGES:
        return symbol
    return symbol.replace(".", "-") + RESEARCH_SUFFIX.get(exchange, f".{exchange}")


def _region(exchange: str) -> str:
    return "US" if exchange in US_EXCHANGES else "EUROPE"


def _bars_to_snapshot(listing: MarketListing, symbol: str, bars: Sequence[DailyBar], *, fetched_at: datetime,
                      start: date, currency: str) -> HistorySnapshot:
    return HistorySnapshot(
        listing_key=listing.key, yahoo_symbol=symbol, mapping_version=MAPPING_VERSION, fetched_at=fetched_at,
        start_session=start, end_session=fetched_at.date(), price_unit=currency, source=HISTORY_SOURCE,
        adjustment_basis=ADJUSTMENT_BASIS,
        # IB TRADES bars are split-adjusted; the close is the only adjusted series available.
        bars=tuple(DailyHistoryBar(b.day, b.open, b.high, b.low, b.close, b.volume, b.close)
                   for b in bars if start <= b.day <= fetched_at.date()),
    )


def _mapping(listing: MarketListing, symbol: str) -> SymbolMappingResult:
    return SymbolMappingResult(listing.key, PROVIDER_ID, MAPPING_VERSION, SymbolMappingStatus.RESOLVED,
                               (symbol,), MAPPING_RULE)


def _verification(mapping: SymbolMappingResult, info, bars: Sequence[DailyBar] | None, error: str | None, *,
                  checked_at: datetime, start: date) -> MarketDataVerification:
    requested_start = datetime.combine(start, time(), timezone.utc)
    requested_end = checked_at + timedelta(seconds=1)
    evidence = (("ibkr.conid", str(info.conid)), ("ibkr.currency", info.currency.upper()),
                ("ibkr.primary_exchange", info.primary_exchange.upper()), ("ibkr.symbol", info.symbol.upper()))
    if bars:
        valid = [b for b in bars if start <= b.day <= checked_at.date()]
        latest = datetime.combine(max(b.day for b in valid), time(), timezone.utc) if valid else None
        if valid and latest <= checked_at:
            return MarketDataVerification(
                mapping, MarketDataIdentityStatus.VERIFIED, MarketDataAvailabilityStatus.AVAILABLE, checked_at,
                checked_at + timedelta(hours=24), requested_start, requested_end, VERIFICATION_VERSION,
                valid_bar_count=len(valid), latest_bar_at=latest, identity_evidence=evidence,
            )
    status = MarketDataAvailabilityStatus.NO_DATA
    code = "IBKR_NO_HISTORY"
    if error and error.startswith("BrokerPermissionError"):
        code = "IBKR_NO_DATA_PERMISSION"
    elif error and error.startswith("BrokerClientError"):
        status, code = MarketDataAvailabilityStatus.PROVIDER_ERROR, "IBKR_PROVIDER_ERROR"
    return MarketDataVerification(
        mapping, MarketDataIdentityStatus.VERIFIED, status, checked_at, checked_at + timedelta(hours=1),
        requested_start, requested_end, VERIFICATION_VERSION, identity_evidence=evidence,
        diagnostics=(MarketDataDiagnostic(code, error or "no daily bars in the requested window"),),
    )


def _verification_dict(value: MarketDataVerification) -> dict:
    return json.loads(json.dumps(asdict(value), default=encode))


def fx_rates_from_bars(currency: str, bars: Sequence[DailyBar], *, known_at: datetime) -> tuple[SessionFXRate, ...]:
    """EUR.<ccy> bars (``ccy`` per EUR) -> EUR per unit of ``ccy`` for each exact session date."""

    return tuple(
        SessionFXRate(b.day, currency.upper(), 1.0 / b.close, FX_SOURCE.replace("<ccy>", currency.upper()), known_at)
        for b in bars
        if isinstance(b.close, (int, float)) and math.isfinite(b.close) and b.close > 0
    )


CalendarFactory = Callable[[str, date, date], SessionCalendar | None]


def default_calendar_factory(provider: ExchangeSessionCalendarProvider | None = None) -> CalendarFactory:
    provider = provider or ExchangeSessionCalendarProvider()
    cache: dict[tuple[str, date, date], SessionCalendar | None] = {}

    def build(exchange: str, start: date, end: date) -> SessionCalendar | None:
        key = (exchange, start, end)
        if key not in cache:
            # Unsupported venue: no calendar, the quality gate stays UNDETERMINED (fail-closed).
            cache[key] = provider.build(exchange, start=start, end=end).calendar if exchange in CALENDAR_BINDINGS else None
        return cache[key]

    return build


@dataclass
class ScannerReports:
    eligibility: dict
    mapping: dict
    history: dict
    instrument_refs: list[InstrumentRef]
    summary: dict = field(default_factory=dict)

    def write(self, directory) -> dict[str, str]:
        from pathlib import Path

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        paths = {}
        for name, body in (("eligibility.json", self.eligibility), ("mapping.json", self.mapping),
                           ("history.json", self.history), ("summary.json", self.summary)):
            path = directory / name
            path.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            paths[name] = str(path)
        return paths


def build_scanner_reports(
    session: GatewaySession,
    acquisition: UniverseAcquisition,
    *,
    as_of: datetime | None = None,
    history_duration: str = "1 Y",
    history_days: int = 370,
    max_listings: int | None = None,
    exclude_conids: frozenset[int] = frozenset(),
    calendar_factory: CalendarFactory | None = None,
    progress: Callable[[str], None] | None = None,
) -> ScannerReports:
    """Eligibility, mapping and history-quality reports for the acquired universe.

    ``max_listings`` bounds the Gateway calls (best scanner rank first);
    ``exclude_conids`` skips instruments already held (they are watched as
    positions, not admitted as new candidates).
    """

    as_of = as_of or datetime.now(timezone.utc)
    calendar_factory = calendar_factory or default_calendar_factory()
    start = as_of.date() - timedelta(days=history_days)
    selected = [item for item in acquisition.listings if item.conid not in exclude_conids]
    if max_listings is not None:
        selected = selected[:max_listings]

    decisions, mappings, results, refs = [], [], [], []
    counts: dict[str, int] = {}
    entries = []

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    for item in selected:
        try:
            info = session.contract_info(item.conid)
        except (BrokerClientError, BrokerNoDataError) as exc:
            bump("contract_details_error")
            if progress:
                progress(f"{item.symbol}@{item.exchange}: no contract details ({exc})")
            continue
        exchange = (info.primary_exchange or item.exchange).upper()
        currency = (info.currency or item.currency).upper()
        raw_type = STOCK_TYPE_MAP.get(info.stock_type.strip().upper()) if info.stock_type else None
        listing = MarketListing(info.symbol or item.symbol, exchange, exchange, _region(exchange), currency=currency,
                                instrument_type=raw_type or (info.stock_type or None), name=info.long_name or None)
        decision = evaluate_instrument_eligibility(listing)
        decisions.append({
            "exchange": listing.exchange, "symbol": listing.symbol, "currency": currency,
            "raw_instrument_type": listing.instrument_type, "isin": None, "name": listing.name,
            "canonical_type": decision.canonical_type.value, "status": decision.status.value,
            "reason_code": decision.reason_code.value,
            "review_flags": [flag.value for flag in decision.review_flags],
            "policy_id": decision.policy_id, "policy_version": decision.policy_version,
            "ibkr": {"conid": info.conid, "stock_type": info.stock_type, "best_rank": item.best_rank,
                     "sources": list(item.sources)},
        })
        bump(f"eligibility:{decision.status.value}")
        if decision.status is not InstrumentEligibilityStatus.ELIGIBLE:
            continue
        symbol = research_symbol(listing.symbol, exchange)
        mapping = _mapping(listing, symbol)
        mappings.append({"exchange": listing.exchange, "symbol": listing.symbol, "mapping_status": "RESOLVED",
                         "candidate_symbols": [symbol], "rule_id": MAPPING_RULE, "diagnostics": [],
                         "ibkr_conid": info.conid})
        bars, error = None, None
        try:
            bars = session.daily_history(info.conid, "SMART", duration=history_duration)
        except BrokerPermissionError as exc:
            error = f"BrokerPermissionError: {exc}"
        except (BrokerClientError, BrokerNoDataError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        verification = _verification(mapping, info, bars, error, checked_at=as_of, start=start)
        entries.append((listing, symbol, info, bars, verification))
        refs.append(InstrumentRef(ticker=symbol, conid=info.conid, exchange="SMART", asset_kind="STK",
                                  currency=currency))

    fx_rates: list[SessionFXRate] = []
    fx_errors = {}
    for currency in sorted({e[0].currency for e in entries if e[3]} - {"EUR"}):
        ccy = "GBP" if currency in {"GBX", "GBP"} else currency
        try:
            fx_rates.extend(fx_rates_from_bars(ccy, session.fx_daily_history(ccy, duration=history_duration),
                                               known_at=as_of))
        except (BrokerClientError, BrokerNoDataError) as exc:
            fx_errors[ccy] = f"{type(exc).__name__}: {exc}"  # liquidity stays UNDETERMINED for this currency

    for listing, symbol, info, bars, verification in entries:
        record = {"listing_key": asdict(listing.key), "research_symbol": symbol, "ibkr_conid": info.conid,
                  "acquisition": {"verification": _verification_dict(verification)}, "quality": None}
        if bars:
            snapshot = _bars_to_snapshot(listing, symbol, bars, fetched_at=as_of, start=start,
                                         currency=listing.currency)
            calendar = calendar_factory(listing.exchange, start, as_of.date())
            quality = evaluate_history_quality(
                snapshot=snapshot, verification=verification, as_of=as_of, calendar=calendar,
                fx_rates=tuple(r for r in fx_rates
                               if r.currency == ("GBP" if listing.currency in {"GBX", "GBP"} else listing.currency)),
            )
            record["quality"] = history_quality_result_to_dict(quality)
            bump(f"history_route:{quality.route.value}")
            if progress:
                gates = ",".join(f"{g.name}={g.status.value}" for g in quality.gates if g.status.value != "PASS")
                progress(f"{symbol}: {quality.route.value}" + (f" ({gates})" if gates else ""))
        else:
            bump("history_unavailable")
            if progress:
                progress(f"{symbol}: no history ({verification.diagnostics[0].message})")
        results.append(record)

    eligibility = {"audit_id": "E2E-S2.2B", "source": "ibkr-scanner", "all_decisions": decisions,
                   "decision_count": len(decisions)}
    mapping = {"audit_id": "E2E-S2.2C-MAPPING", "provider_id": PROVIDER_ID, "mapping_version": MAPPING_VERSION,
               "mappings": mappings, "mapping_count": len(mappings)}
    history = {"audit_id": "E2E-S2.2D-HISTORY-PILOT", "source": "ibkr-gateway", "as_of": as_of.isoformat(),
               "results": results, "fx_errors": fx_errors}
    summary = {
        "audit_id": "E2E-S5.0-5-SCANNER",
        "plan": asdict(acquisition.plan),
        "plan_fingerprint": acquisition.plan.fingerprint(),
        "acquired_at": acquisition.acquired_at.isoformat(),
        "as_of": as_of.isoformat(),
        "scans": [{"location": s.query.location, "scan_code": s.query.scan_code, "rows": len(s.rows),
                   "error": s.error} for s in acquisition.scans],
        "distinct_listings": len(acquisition.listings),
        "excluded_held": sum(1 for item in acquisition.listings if item.conid in exclude_conids),
        "evaluated_listings": len(selected),
        "counts": dict(sorted(counts.items())),
        "fx_currencies": sorted({r.currency for r in fx_rates}),
        "fx_errors": fx_errors,
    }
    return ScannerReports(eligibility, mapping, history, refs, summary)
