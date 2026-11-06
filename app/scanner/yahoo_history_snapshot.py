"""Acquire one Yahoo frame for identity verification and history gates."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from numbers import Real
from importlib.metadata import version
from zoneinfo import ZoneInfo

import pandas as pd

from app.scanner.history_quality_contracts import DailyHistoryBar, HistorySnapshot, day, aware
from app.scanner.market_data_contracts import MarketDataDiagnostic, MarketDataVerification
from app.scanner.yahoo_market_data_verifier import YahooMarketDataVerifier, utc_now


ADAPTER_VERSION = "scanner-yahoo-history-v1"


@dataclass(frozen=True)
class HistoryAcquisition:
    verification: MarketDataVerification
    snapshot: HistorySnapshot | None
    diagnostics: tuple[MarketDataDiagnostic, ...]
    ambiguous_zero_volume_sessions: tuple[date, ...] = ()
    adapter_version: str = ADAPTER_VERSION


def _number(value):
    if value is None or value is pd.NA or (isinstance(value, Real) and pd.isna(value)):
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("Nonnumeric history observation")
    return float(value)  # Keep negatives/infinities visible to quality gates.


def frame_to_snapshot(frame, *, listing, mapping, metadata, start, end, fetched_at, timezone,
                      provider_version="injected"):
    if (not isinstance(frame, pd.DataFrame) or frame.empty
            or not frame.columns.is_unique or isinstance(frame.columns, pd.MultiIndex)
            or not all(c in frame for c in ("Open", "High", "Low", "Close"))):
        raise ValueError("Invalid history schema")
    if (not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None
            or frame.index.has_duplicates or frame.index.hasnans):
        raise ValueError("Invalid daily timestamps")
    local = frame.index.tz_convert(ZoneInfo(timezone))
    if any(t != t.normalize() for t in local) or len(set(local.date)) != len(local):
        raise ValueError("History timestamps are not unique local session midnights")
    if any(not start <= d <= end for d in local.date):
        raise ValueError("History outside requested session range")
    unit = metadata.get("currency")
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("Missing provider price unit")
    bars = []
    # Preserve a single row alignment; never dropna each column independently.
    for position, session in enumerate(local.date):
        row = frame.iloc[position]
        volume = _number(row.get("Volume"))
        # yfinance can fill missing volume with 0 before returning the frame.
        # Without raw-provider evidence these zeroes cannot prove inactivity.
        if volume == 0:
            volume = None
        bars.append(DailyHistoryBar(session, *(_number(row[c]) for c in ("Open", "High", "Low", "Close")),
                                    volume, _number(row.get("Adj Close"))))
    bars.sort(key=lambda b: b.session)
    return HistorySnapshot(listing.key, mapping.resolved_symbol, mapping.mapping_version,
        fetched_at, start, end, unit.strip(), f"yfinance:{provider_version}:{mapping.resolved_symbol}",
        "Yahoo Close and Volume as supplied; Adj Close separately; auto_adjust=False; back_adjust=False; repair=False",
        tuple(bars))


class _CaptureTicker:
    def __init__(self, ticker):
        self.ticker = ticker
        self.frame = None
        self.metadata = None

    def history(self, **kwargs):
        self.frame = self.ticker.history(**kwargs, keepna=True, back_adjust=False)
        return self.frame

    def get_history_metadata(self):
        self.metadata = self.ticker.get_history_metadata()
        return self.metadata


class YahooHistorySnapshotProvider:
    def __init__(self, *, ticker_factory=None, timeout_seconds=20., now=utc_now):
        self.ticker_factory, self.timeout_seconds, self.now = ticker_factory, timeout_seconds, now

    def fetch(self, listing, mapping, *, start, end, timezone):
        day(start)
        day(end)
        begun = self.now()
        aware(begun)
        zone = ZoneInfo(timezone)
        if start > end or end > begun.astimezone(zone).date():
            raise ValueError("Invalid or future session interval")
        request_start = datetime.combine(start, time.min, zone)
        request_end = min(datetime.combine(end + timedelta(days=1), time.min, zone), begun)
        captures = []
        provider_version = "injected" if self.ticker_factory is not None else version("yfinance")
        def factory(symbol):
            ticker_factory = self.ticker_factory
            if ticker_factory is None:
                import yfinance as yf
                ticker_factory = yf.Ticker
            capture = _CaptureTicker(ticker_factory(symbol))
            captures.append(capture)
            return capture
        verifier = YahooMarketDataVerifier(ticker_factory=factory,
                                          timeout_seconds=self.timeout_seconds, now=self.now)
        verification = verifier.verify(listing, mapping, start=request_start, end=request_end)
        diagnostics = []
        snapshot = None
        zero_sessions = ()
        if captures and isinstance(captures[0].frame, pd.DataFrame) and not captures[0].frame.empty:
            captured = captures[0]
            if not isinstance(captured.metadata, dict):
                diagnostics.append(MarketDataDiagnostic("SNAPSHOT_METADATA_UNAVAILABLE", "History cannot be bound to provider metadata"))
            else:
                try:
                    snapshot = frame_to_snapshot(captured.frame, listing=listing, mapping=mapping,
                        metadata=captured.metadata, start=start, end=end,
                        fetched_at=verification.checked_at, timezone=timezone, provider_version=provider_version)
                    local = captured.frame.index.tz_convert(
                        ZoneInfo(timezone)
                    )
                    price_columns = (
                        "Open",
                        "High",
                        "Low",
                        "Close",
                    )
                    empty_price_sessions = tuple(
                        sorted(
                            local[position].date()
                            for position in range(
                                len(captured.frame)
                            )
                            if all(
                                pd.isna(
                                    captured.frame.iloc[
                                        position
                                    ][column]
                                )
                                for column in price_columns
                            )
                        )
                    )
                    if empty_price_sessions:
                        diagnostics.append(
                            MarketDataDiagnostic(
                                "EMPTY_PROVIDER_SESSION",
                                (
                                    f"{len(empty_price_sessions)} "
                                    "provider session rows have no "
                                    "OHLC values; retained for "
                                    "downstream price and freshness "
                                    "gates"
                                ),
                            )
                        )
                    if "Volume" in captured.frame:
                        zero_sessions = tuple(sorted(local[i].date() for i, v in enumerate(captured.frame["Volume"])
                                                     if isinstance(v, Real) and v == 0))
                        if zero_sessions:
                            diagnostics.append(MarketDataDiagnostic("ZERO_VOLUME_AMBIGUOUS",
                                f"{len(zero_sessions)} zero volumes retained as unknown because provider preprocessing may fill missing values"))
                    if "Adj Close" not in captured.frame:
                        diagnostics.append(MarketDataDiagnostic("ADJUSTED_CLOSE_MISSING", "Adjusted indicators remain unavailable; Close was not substituted"))
                except (ValueError, TypeError, OverflowError):
                    diagnostics.append(MarketDataDiagnostic("INVALID_HISTORY_SNAPSHOT", "Cannot construct an unambiguous daily snapshot"))
        return HistoryAcquisition(verification, snapshot, tuple(diagnostics), zero_sessions)
