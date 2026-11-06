"""Read-only IB Gateway / TWS session (S5.0-1, architecture G5 and §5D).

Verified on the operator's PC on 2026-11-06 with IB Gateway (IB API, Live,
"Read-Only API" ticked, port 4001) and ``ib_async`` 2.1.0.

Two independent locks keep it read-only:

* the Gateway's own "Read-Only API" setting refuses any order on the server;
* this session always connects with ``readonly=True`` and exposes only data
  requests (contract details, daily history, FX history, market scanners,
  news). There is no method that
  could place, modify or cancel anything.

``ib_async`` is imported lazily so offline tests and CI never need it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from .ibkr_client import BrokerClientError, BrokerNoDataError

NO_PERMISSION_CODES = frozenset({162, 354, 10167, 10168})  # no market-data permission / subscription
NO_DATA_CODES = frozenset({200, 10197})  # no security definition / no data
_HEADLINE_META = re.compile(r"^\{[^}]*\}")
NEWS_ATTEMPTS = 3


class BrokerPermissionError(BrokerNoDataError):
    """The account lacks a market-data permission for this listing (IB error 162)."""


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class ContractInfo:
    conid: int
    symbol: str
    exchange: str
    primary_exchange: str
    currency: str
    long_name: str
    sec_type: str
    stock_type: str = ""  # IBKR ContractDetails.stockType: COMMON, ETF, ADR, REIT, PREFERRED, ...


@dataclass(frozen=True)
class ScannerRow:
    rank: int
    conid: int
    symbol: str
    exchange: str  # primary listing exchange
    currency: str


@dataclass(frozen=True)
class Headline:
    published_at: datetime
    provider_code: str
    article_id: str
    headline: str


class GatewaySession(Protocol):
    def contract_info(self, conid: int, exchange: str = "") -> ContractInfo: ...

    def daily_history(self, conid: int, exchange: str, *, duration: str = "1 Y") -> list[DailyBar]: ...

    def news_providers(self) -> list[str]: ...

    def headlines(self, conid: int, provider_codes: list[str], *, limit: int = 20) -> list[Headline]: ...

    def scanner(self, instrument: str, location: str, scan_code: str, *, rows: int = 50,
                market_cap_above_musd: float | None = None) -> list[ScannerRow]: ...

    def fx_daily_history(self, currency: str, *, duration: str = "1 Y") -> list[DailyBar]:
        """Daily EUR.<currency> mid bars: close = units of ``currency`` per 1 EUR."""
        ...


def scan_key(instrument: str, location: str, scan_code: str) -> str:
    return f"{instrument}|{location}|{scan_code}"


def clean_headline(text: str) -> str:
    """Strip the ``{A:800015:L:en}`` language/attribute prefix IBKR puts on headlines."""

    return _HEADLINE_META.sub("", text or "").strip()


class IbAsyncGatewaySession:
    """``GatewaySession`` over ``ib_async``; always ``readonly=True``."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4001, client_id: int = 51, timeout: float = 20.0) -> None:
        from ib_async import IB

        self._ib = IB()
        self._errors: dict[int, tuple[int, str]] = {}
        self._ib.errorEvent += self._on_error
        self._ib.connect(host, port, clientId=client_id, readonly=True, timeout=timeout)

    def _on_error(self, req_id: int, code: int, message: str, *_: Any) -> None:
        self._errors[req_id] = (code, message)

    def close(self) -> None:
        self._ib.disconnect()

    def __enter__(self) -> "IbAsyncGatewaySession":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _raise_last_error(self, what: str) -> None:
        if not self._errors:
            return
        code, message = list(self._errors.values())[-1]
        self._errors.clear()
        if code in NO_PERMISSION_CODES:
            raise BrokerPermissionError(f"{what}: IB error {code}: {message}")
        if code in NO_DATA_CODES:
            raise BrokerNoDataError(f"{what}: IB error {code}: {message}")
        raise BrokerClientError(f"{what}: IB error {code}: {message}")

    def _contract(self, conid: int, exchange: str = ""):
        from ib_async import Contract

        return Contract(conId=conid, exchange=exchange)

    def contract_info(self, conid: int, exchange: str = "") -> ContractInfo:
        self._errors.clear()
        details = self._ib.reqContractDetails(self._contract(conid, exchange))
        if not details:
            self._raise_last_error(f"contract {conid}")
            raise BrokerNoDataError(f"contract {conid}: no contract details")
        d = details[0]
        c = d.contract
        return ContractInfo(
            conid=c.conId, symbol=c.symbol, exchange=c.exchange, primary_exchange=c.primaryExchange or c.exchange,
            currency=c.currency, long_name=d.longName or "", sec_type=c.secType,
            stock_type=getattr(d, "stockType", "") or "",
        )

    def daily_history(self, conid: int, exchange: str, *, duration: str = "1 Y") -> list[DailyBar]:
        self._errors.clear()
        bars = self._ib.reqHistoricalData(
            self._contract(conid, exchange), endDateTime="", durationStr=duration, barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=2, timeout=60,
        )
        if not bars:
            self._raise_last_error(f"history {conid}@{exchange}")
            raise BrokerNoDataError(f"history {conid}@{exchange}: no bars")
        return [DailyBar(b.date if isinstance(b.date, date) and not isinstance(b.date, datetime) else b.date.date(),
                         b.open, b.high, b.low, b.close, b.volume) for b in bars]

    def fx_daily_history(self, currency: str, *, duration: str = "1 Y") -> list[DailyBar]:
        from ib_async import Contract

        self._errors.clear()
        contract = Contract(secType="CASH", symbol="EUR", currency=currency.upper(), exchange="IDEALPRO")
        bars = self._ib.reqHistoricalData(
            contract, endDateTime="", durationStr=duration, barSizeSetting="1 day",
            whatToShow="MIDPOINT", useRTH=False, formatDate=2, timeout=60,
        )
        if not bars:
            self._raise_last_error(f"fx EUR.{currency}")
            raise BrokerNoDataError(f"fx EUR.{currency}: no bars")
        return [DailyBar(b.date if isinstance(b.date, date) and not isinstance(b.date, datetime) else b.date.date(),
                         b.open, b.high, b.low, b.close, 0.0) for b in bars]

    def scanner(self, instrument: str, location: str, scan_code: str, *, rows: int = 50,
                market_cap_above_musd: float | None = None) -> list[ScannerRow]:
        from ib_async import ScannerSubscription

        self._errors.clear()
        sub = ScannerSubscription(instrument=instrument, locationCode=location, scanCode=scan_code,
                                  numberOfRows=rows)
        if market_cap_above_musd is not None:
            sub.marketCapAbove = market_cap_above_musd
        data = self._ib.reqScannerData(sub)
        self._ib.sleep(0.5)  # let the Gateway settle the cancel (benign error 162) before the next scan
        if not data:
            errors = [e for e in self._errors.values() if "scanner subscription cancelled" not in e[1].lower()]
            self._errors.clear()
            if errors:
                code, message = errors[-1]
                raise BrokerClientError(f"scanner {location} {scan_code}: IB error {code}: {message}")
            return []
        self._errors.clear()
        out = []
        for r in data:
            c = r.contractDetails.contract
            out.append(ScannerRow(rank=r.rank, conid=c.conId, symbol=c.symbol,
                                  exchange=c.primaryExchange or c.exchange, currency=c.currency))
        return out

    def news_providers(self) -> list[str]:
        return [p.code for p in self._ib.reqNewsProviders()]

    def headlines(self, conid: int, provider_codes: list[str], *, limit: int = 20) -> list[Headline]:
        if not provider_codes:
            return []
        self._errors.clear()
        news = None
        for _ in range(NEWS_ATTEMPTS):  # ib_async gives up after a fixed 4 s (AMZN timed out once in the 2026-11-06 recording)
            news = self._ib.reqHistoricalNews(conid, "+".join(provider_codes), "", "", limit)
            if news is not None:
                break
        if news is None:
            raise BrokerClientError(f"news {conid}: timeout after {NEWS_ATTEMPTS} attempts")
        return [Headline(n.time, n.providerCode, n.articleId, clean_headline(n.headline)) for n in news]


@dataclass(frozen=True)
class HistoryResult:
    bars: list[DailyBar]
    listing: ContractInfo
    substituted_for: int | None  # conid of the held listing when another listing of the same ISIN was used
    notes: tuple[str, ...]


def history_with_listing_fallback(
    session: GatewaySession,
    conid: int,
    exchange: str,
    alternatives: tuple[int, ...] | list[int] = (),
    *,
    duration: str = "1 Y",
) -> HistoryResult:
    """Daily history of the held listing; if the account has no data permission for
    it (e.g. Milan ETFs, IB error 162), the first other listing of the same ISIN in
    the same currency is used and recorded as a substitution. Never mixes listings
    within one series and never converts currencies."""

    primary = session.contract_info(conid, exchange)
    try:
        return HistoryResult(session.daily_history(conid, exchange, duration=duration), primary, None, ())
    except BrokerPermissionError as exc:
        notes = [f"no data permission for {primary.symbol}@{exchange}: {exc}"]
    for alt in alternatives:
        try:
            info = session.contract_info(alt)
        except BrokerClientError as exc:
            notes.append(f"listing {alt}: {exc}")
            continue
        if info.currency != primary.currency or info.sec_type != primary.sec_type:
            notes.append(f"listing {alt} {info.symbol}@{info.exchange} skipped: {info.currency}/{info.sec_type}")
            continue
        try:
            bars = session.daily_history(alt, info.exchange, duration=duration)
        except BrokerNoDataError as exc:
            notes.append(f"listing {alt} {info.symbol}@{info.exchange}: {exc}")
            continue
        notes.append(f"history from {info.symbol}@{info.exchange} (conid {alt}) instead of {primary.symbol}@{exchange}")
        return HistoryResult(bars, info, conid, tuple(notes))
    raise BrokerPermissionError("; ".join(notes) + "; no permitted listing in the same currency")


class RecordedGatewaySession:
    """``GatewaySession`` replaying a ``tools.s5_record_gateway_fixtures`` recording (offline runs, tests)."""

    def __init__(self, recording: dict | str) -> None:
        import json
        from pathlib import Path

        data = json.loads(Path(recording).read_text(encoding="utf-8")) if isinstance(recording, str) else recording
        self._providers = list(data.get("news_providers") or [])
        self._instruments = {int(k): v for k, v in (data.get("instruments") or {}).items()}
        self._scans = dict(data.get("scans") or {})
        self._fx = dict(data.get("fx") or {})

    def _entry(self, conid: int) -> dict:
        entry = self._instruments.get(conid)
        if entry is None:
            raise BrokerNoDataError(f"conid {conid} not in recording")
        return entry

    def contract_info(self, conid: int, exchange: str = "") -> ContractInfo:
        c = self._entry(conid).get("contract")
        if not c:
            raise BrokerNoDataError(f"conid {conid}: no recorded contract")
        return ContractInfo(**c)

    def daily_history(self, conid: int, exchange: str, *, duration: str = "1 Y") -> list[DailyBar]:
        entry = self._entry(conid)
        if "history_error" in entry:
            raise BrokerClientError(entry["history_error"])
        bars = entry.get("bars") or []
        if not bars:
            raise BrokerNoDataError(f"conid {conid}: no recorded bars")
        return [DailyBar(date.fromisoformat(b["day"]), b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in bars]

    def news_providers(self) -> list[str]:
        return list(self._providers)

    def headlines(self, conid: int, provider_codes: list[str], *, limit: int = 20) -> list[Headline]:
        entry = self._entry(conid)
        if "news_error" in entry:
            raise BrokerClientError(entry["news_error"])
        return [
            Headline(datetime.fromisoformat(h["published_at"]), h["provider_code"], h["article_id"], h["headline"])
            for h in (entry.get("headlines") or [])[:limit]
        ]

    def scanner(self, instrument: str, location: str, scan_code: str, *, rows: int = 50,
                market_cap_above_musd: float | None = None) -> list[ScannerRow]:
        entry = self._scans.get(scan_key(instrument, location, scan_code))
        if entry is None:
            raise BrokerNoDataError(f"scan {location} {scan_code} not in recording")
        if isinstance(entry, dict) and "error" in entry:
            raise BrokerClientError(entry["error"])
        return [ScannerRow(**row) for row in entry][:rows]

    def fx_daily_history(self, currency: str, *, duration: str = "1 Y") -> list[DailyBar]:
        bars = self._fx.get(currency.upper())
        if not bars:
            raise BrokerNoDataError(f"fx EUR.{currency} not in recording")
        return [DailyBar(date.fromisoformat(b["day"]), b["open"], b["high"], b["low"], b["close"], 0.0) for b in bars]
