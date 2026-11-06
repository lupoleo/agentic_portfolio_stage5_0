"""Yahoo verification boundary, independent of the frozen portfolio provider."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from types import MappingProxyType
from typing import Callable

import pandas as pd

from app.scanner.market_data_contracts import (
    MarketDataAvailabilityStatus as Availability,
    MarketDataDiagnostic as Diagnostic,
    MarketDataIdentityStatus as Identity,
    MarketDataVerification, SymbolMappingResult, SymbolMappingStatus,
)
from app.scanner.universe_models import MarketListing


VERIFICATION_VERSION = "scanner-yahoo-verification-v1"
YAHOO_VENUE_CODES = MappingProxyType({
    "AMEX": frozenset({"ASE"}), "BATS": frozenset({"BTS", "BATS"}),
    "NASDAQ": frozenset({"NMS", "NGM", "NCM"}), "NYSE": frozenset({"NYQ"}),
    "NYSE ARCA": frozenset({"PCX"}), "AS": frozenset({"AMS"}),
    "AT": frozenset({"ATH"}), "BIT": frozenset({"MIL"}),
    "BR": frozenset({"BRU"}), "BUD": frozenset({"BUD"}),
    "CO": frozenset({"CPH"}), "HE": frozenset({"HEL"}),
    "LS": frozenset({"LIS"}), "LSE": frozenset({"LSE"}),
    "MC": frozenset({"MCE"}), "OL": frozenset({"OSL"}),
    "PA": frozenset({"PAR"}), "PR": frozenset({"PRA"}),
    "ST": frozenset({"STO"}), "SW": frozenset({"EBS"}),
    "VI": frozenset({"VIE"}), "WAR": frozenset({"WSE"}),
    "XETRA": frozenset({"GER"}),
})
_TYPE_CODES = {"COMMON STOCK": "EQUITY", "COMMON_STOCK": "EQUITY",
               "ETF": "ETF", "MUTUAL FUND": "MUTUALFUND"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _currency(value):
    value = _text(value)
    # Yahoo uses case-sensitive GBp for pence, distinct from GBP.
    return "GBX" if value == "GBp" else value.upper() if value else None


def verify_identity(listing, mapping, metadata):
    evidence = []
    diagnostics = []
    mismatches = []
    missing = []
    for field in ("symbol", "exchangeName", "currency", "instrumentType",
                  "isin", "shortName", "longName", "exchangeTimezoneName"):
        value = _text(metadata.get(field))
        if value:
            evidence.append(("yahoo." + field, value))
    expected = {
        "symbol": mapping.resolved_symbol,
        "exchangeName": YAHOO_VENUE_CODES.get(listing.exchange),
        "currency": _currency(listing.currency),
        "instrumentType": _TYPE_CODES.get(listing.instrument_type),
    }
    for field, wanted in expected.items():
        actual = _text(metadata.get(field))
        if actual is None or wanted is None:
            missing.append(field)
            continue
        if field == "exchangeName":
            matches = actual.upper() in wanted
        elif field == "currency":
            actual_unit = _currency(actual)
            matches = actual_unit == wanted
            if not matches and listing.exchange == "LSE" and {actual_unit, wanted} == {"GBP", "GBX"}:
                matches = True
                evidence.append(("price_unit_to_listing_currency",
                                 "0.01" if actual_unit == "GBX" else "100"))
        else:
            matches = actual.upper() == wanted.upper()
        if not matches:
            mismatches.append(field)
    source_isin = _text(listing.isin)
    observed_isin = _text(metadata.get("isin"))
    if source_isin and observed_isin and source_isin.upper() != observed_isin.upper():
        mismatches.append("isin")
    if mismatches:
        diagnostics.append(Diagnostic("IDENTITY_MISMATCH", "Contradictory fields: " + ", ".join(mismatches)))
        return Identity.MISMATCH, tuple(evidence), tuple(diagnostics)
    if missing:
        diagnostics.append(Diagnostic("IDENTITY_INCOMPLETE", "Missing evidence/rules: " + ", ".join(missing)))
        return Identity.UNVERIFIED, tuple(evidence), tuple(diagnostics)
    evidence.append(("identity_rule", "exact-symbol+venue+currency+type-v1"))
    return Identity.VERIFIED, tuple(evidence), ()


def classify_exception(exc):
    """No raw exception text/URLs in reports; missing != nonexistent."""
    names = {cls.__name__ for cls in type(exc).__mro__}
    response = getattr(exc, "response", None)
    http = getattr(response, "status_code", None)
    if "YFRateLimitError" in names or http == 429:
        return Availability.TEMPORARY_ERROR, Diagnostic("RATE_LIMIT", "Provider rate limit")
    if isinstance(exc, (TimeoutError, ConnectionError)) or names & {
        "Timeout", "ReadTimeout", "ConnectTimeout", "ConnectionError",
    } or getattr(exc, "code", None) in {6, 7, 28, 52, 56} or http in {408, 500, 502, 503, 504}:
        return Availability.TEMPORARY_ERROR, Diagnostic("NETWORK_ERROR", "Transient network failure")
    if names & {"YFPricesMissingError", "YFTzMissingError", "YFTickerMissingError"}:
        return Availability.PROVIDER_ERROR, Diagnostic(
            "YAHOO_MISSING_DATA_INDETERMINATE",
            "Provider reported missing prices/timezone; symbol existence is undetermined",
        )
    return Availability.PROVIDER_ERROR, Diagnostic("PROVIDER_EXCEPTION", "Provider request failed")


def validate_bars(frame, start, end, checked_at):
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("history must be a DataFrame")
    if frame.empty:
        return 0, None, ()
    columns = ["Open", "High", "Low", "Close"]
    if not frame.columns.is_unique or not all(c in frame.columns for c in columns):
        raise ValueError("history lacks unique OHLC columns")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("history requires timezone-aware DatetimeIndex")
    if frame.index.has_duplicates or frame.index.hasnans:
        raise ValueError("duplicate or invalid timestamps")
    values = frame[columns].apply(pd.to_numeric, errors="coerce")
    valid = values.apply(lambda col: col.map(lambda n: math.isfinite(n) and n > 0)).all(axis=1)
    valid &= values["High"] >= values[["Open", "Close", "Low"]].max(axis=1)
    valid &= values["Low"] <= values[["Open", "Close", "High"]].min(axis=1)
    if "Volume" in frame.columns:
        volume = pd.to_numeric(frame["Volume"], errors="coerce")
        valid &= volume.map(lambda n: math.isfinite(n) and n >= 0)
    index = frame.index.tz_convert("UTC")
    valid &= (index >= start) & (index < end) & (index <= checked_at)
    selected = index[valid.to_numpy()]
    rejected = len(frame) - len(selected)
    diagnostics = (Diagnostic("BARS_REJECTED", f"Rejected {rejected} invalid/out-of-window bars"),) if rejected else ()
    return len(selected), selected.max().to_pydatetime() if len(selected) else None, diagnostics


class YahooMarketDataVerifier:
    def __init__(self, *, timeout_seconds=20.0, ttl=timedelta(hours=24),
                 ticker_factory=None, now: Callable[[], datetime] = utc_now):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        self.timeout_seconds = timeout_seconds
        self.ttl = ttl
        self.ticker_factory = ticker_factory
        self.now = now

    def verify(self, listing: MarketListing, mapping: SymbolMappingResult, *,
               start: datetime, end: datetime) -> MarketDataVerification:
        if not isinstance(listing, MarketListing) or not isinstance(mapping, SymbolMappingResult):
            raise TypeError("listing and mapping must use Scanner contracts")
        if mapping.listing_key != listing.key or mapping.provider_id != "yahoo":
            raise ValueError("listing/mapping/provider mismatch")
        begun = self.now()
        for value in (start, end, begun):
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware")
        if start >= end or end > begun:
            raise ValueError("invalid or future request window")

        identity, evidence = Identity.UNVERIFIED, ()
        availability, diagnostics = Availability.NOT_CHECKED, []
        count, latest = 0, None
        if mapping.status is SymbolMappingStatus.RESOLVED:
            try:
                factory = self.ticker_factory
                if factory is None:
                    import yfinance as yf
                    factory = yf.Ticker
                ticker = factory(mapping.resolved_symbol)
                frame = ticker.history(start=start, end=end, interval="1d",
                                       auto_adjust=False, actions=False, repair=False,
                                       timeout=self.timeout_seconds, raise_errors=True)
            except Exception as exc:
                availability, diagnostic = classify_exception(exc)
                diagnostics.append(diagnostic)
            else:
                try:
                    count, latest, bar_diagnostics = validate_bars(frame, start, end, self.now())
                    diagnostics.extend(bar_diagnostics)
                    availability = Availability.AVAILABLE if count else Availability.NO_DATA
                    if not count:
                        diagnostics.append(Diagnostic("NO_USABLE_BARS", "No valid bars in requested window"))
                except (ValueError, TypeError, OverflowError):
                    availability = Availability.PROVIDER_ERROR
                    diagnostics.append(Diagnostic("INVALID_HISTORY", "Malformed price history"))
                # Only access metadata after a nonempty history response. Avoid
                # an implicit metadata/history retry on an empty/missing result.
                if isinstance(frame, pd.DataFrame) and not frame.empty:
                    try:
                        metadata = ticker.get_history_metadata()
                        if not isinstance(metadata, dict):
                            raise ValueError("metadata must be a dict")
                        identity, evidence, identity_diagnostics = verify_identity(listing, mapping, metadata)
                        diagnostics.extend(identity_diagnostics)
                    except Exception as exc:
                        _, diagnostic = classify_exception(exc)
                        diagnostics.append(Diagnostic("METADATA_" + diagnostic.code, diagnostic.message))
        else:
            diagnostics.append(Diagnostic("MAPPING_NOT_RESOLVED", "No network verification attempted"))
        checked = self.now()
        return MarketDataVerification(
            mapping=mapping, identity_status=identity, availability_status=availability,
            checked_at=checked, expires_at=checked + self.ttl,
            requested_start=start, requested_end=end, verification_version=VERIFICATION_VERSION,
            valid_bar_count=count, latest_bar_at=latest, identity_evidence=evidence,
            diagnostics=tuple(diagnostics), from_cache=False,
        )
