"""Market and sector context for research, from IB Gateway daily bars.

Research flagged "broad market and sector price context is missing" as a
material gap on almost every scanner candidate. This module adds one MARKET
evidence item per ticker: how the region benchmark and the instrument's
sector moved over 5/20/60 sessions, and the instrument's relative
performance. Benchmarks and sectors are liquid ETF proxies resolved live by
symbol (no persisted catalog); the sector comes from the IBKR contract
details (industry / category). Everything is read-only and deterministic; a
proxy that cannot be resolved or has no history is left out (fail-soft).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.broker.gateway import GatewaySession
from app.broker.ibkr_client import BrokerClientError, BrokerNoDataError
from app.broker.instrument_directory import InstrumentDirectory

US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "ARCA", "AMEX", "BATS", "NYSE ARCA", "ISLAND"})
WINDOWS = (5, 20, 60)


@dataclass(frozen=True)
class Proxy:
    symbol: str
    primary_exchange: str
    currency: str
    name: str


BENCHMARKS = {
    "US": Proxy("SPY", "ARCA", "USD", "SPDR S&P 500 ETF (S&P 500 proxy)"),
    "EUROPE": Proxy("EXSA", "IBIS", "EUR", "iShares STOXX Europe 600 (STOXX Europe 600 proxy)"),
}

_US_SECTOR = {
    "banks": "XLF", "insurance": "XLF", "financial_services": "XLF", "real_estate": "XLRE",
    "technology": "XLK", "telecom": "XLC", "media": "XLC", "energy": "XLE", "utilities": "XLU",
    "autos": "XLY", "travel_leisure": "XLY", "retail": "XLY", "consumer_discretionary": "XLY",
    "health_care": "XLV", "food_beverage": "XLP", "personal_household": "XLP",
    "industrials": "XLI", "construction": "XLI", "chemicals": "XLB", "basic_resources": "XLB",
}
_EU_SECTOR = {  # iShares STOXX Europe 600 sector ETFs on Xetra
    "banks": "EXV1", "insurance": "EXH5", "financial_services": "EXH2", "real_estate": "EXI5",
    "technology": "EXV3", "telecom": "EXV2", "media": "EXH6", "energy": "EXH1", "utilities": "EXH9",
    "autos": "EXV5", "travel_leisure": "EXV9", "retail": "EXH8", "consumer_discretionary": "EXH7",
    "health_care": "EXV4", "food_beverage": "EXH3", "personal_household": "EXH7",
    "industrials": "EXH4", "construction": "EXV8", "chemicals": "EXV7", "basic_resources": "EXV6",
}
SECTOR_PROXIES = {
    "US": {k: Proxy(v, "ARCA", "USD", f"SPDR sector ETF {v}") for k, v in _US_SECTOR.items()},
    "EUROPE": {k: Proxy(v, "IBIS", "EUR", f"iShares STOXX Europe 600 sector ETF {v}") for k, v in _EU_SECTOR.items()},
}

_CATEGORY_BUCKETS = (
    (("bank",), "banks"),
    (("insurance",), "insurance"),
    (("reit", "real estate"), "real_estate"),
    (("telecom",), "telecom"),
    (("media", "advertising"), "media"),
    (("auto",), "autos"),
    (("airline", "lodging", "leisure", "entertainment"), "travel_leisure"),
    (("retail",), "retail"),
    (("pharma", "health", "biotech"), "health_care"),
    (("food", "beverage"), "food_beverage"),
    (("cosmetic", "personal care", "household"), "personal_household"),
    (("building materials", "engineering&construction", "engineering & construction"), "construction"),
    (("chemical",), "chemicals"),
)
_INDUSTRY_BUCKETS = {
    "financial": "financial_services", "technology": "technology", "communications": "technology",
    "energy": "energy", "utilities": "utilities", "consumer, cyclical": "consumer_discretionary",
    "consumer, non-cyclical": "food_beverage", "industrial": "industrials", "basic materials": "basic_resources",
}


def sector_bucket(industry: str, category: str) -> str | None:
    cat = (category or "").strip().lower()
    for needles, bucket in _CATEGORY_BUCKETS:
        if any(n in cat for n in needles):
            return bucket
    return _INDUSTRY_BUCKETS.get((industry or "").strip().lower())


def _returns(close: pd.Series) -> dict[int, float]:
    out = {}
    for n in WINDOWS:
        if len(close) > n and float(close.iloc[-1 - n]) > 0:
            out[n] = (float(close.iloc[-1]) / float(close.iloc[-1 - n]) - 1.0) * 100
    return out


def _describe(label: str, proxy: Proxy, close: pd.Series) -> tuple[str, dict[int, float]]:
    rets = _returns(close)
    parts = [f"{n}-session {r:.2f}%" for n, r in rets.items()]
    if len(close) >= 50:
        sma50 = float(close.tail(50).mean())
        parts.append(f"close vs SMA50 {(float(close.iloc[-1]) / sma50 - 1) * 100:.2f}%")
    return f"{label}: {proxy.name} ({proxy.symbol}): " + ", ".join(parts) + ".", rets


class MarketContextBuilder:
    def __init__(self, session: GatewaySession, directory: InstrumentDirectory, *, duration: str = "1 Y") -> None:
        self._session = session
        self._directory = directory
        self._duration = duration
        self._proxy_close: dict[Proxy, pd.Series | None] = {}

    def _close(self, proxy: Proxy) -> pd.Series | None:
        if proxy not in self._proxy_close:
            try:
                conid = self._session.resolve_stock(proxy.symbol, proxy.primary_exchange, proxy.currency)
                bars = self._session.daily_history(conid, "SMART", duration=self._duration)
                self._proxy_close[proxy] = pd.Series([b.close for b in bars], index=[b.day for b in bars],
                                                     dtype=float).dropna()
            except (BrokerClientError, BrokerNoDataError):
                self._proxy_close[proxy] = None
        return self._proxy_close[proxy]

    def describe(self, ticker: str, close: pd.Series) -> tuple[str, dict] | None:
        ref = self._directory.get(ticker)
        if ref is None:
            return None
        try:
            info = self._session.contract_info(ref.conid)
        except (BrokerClientError, BrokerNoDataError):
            return None
        region = "US" if (info.primary_exchange or "").upper() in US_EXCHANGES else "EUROPE"
        bucket = sector_bucket(info.industry, info.category)
        stock = _returns(close)
        lines, meta = [], {"region": region, "industry": info.industry, "category": info.category,
                           "sector_bucket": bucket, "proxies": []}
        relative = []
        candidates = [("Region benchmark", BENCHMARKS[region])]
        if bucket is not None:
            candidates.append((f"Sector ({info.industry or '?'} / {info.category or '?'})", SECTOR_PROXIES[region][bucket]))
        for label, proxy in candidates:
            series = self._close(proxy)
            if series is None or len(series) < 6:
                continue
            text, rets = _describe(label, proxy, series)
            lines.append(text)
            meta["proxies"].append(proxy.symbol)
            rel = [f"{n}-session {stock[n] - rets[n]:+.2f} pp" for n in WINDOWS if n in stock and n in rets]
            if rel:
                relative.append(f"vs {proxy.symbol}: " + ", ".join(rel))
        if not lines:
            return None
        text = ("Market and sector context (IB Gateway daily bars of ETF proxies; returns in each instrument's "
                "trading currency). " + " ".join(lines))
        if relative:
            text += f" Relative performance of {ticker}: " + "; ".join(relative) + "."
        return text, meta


# --- Macro context (2026-11-09) ----------------------------------------------------------
# Research coverage counts six dimensions; no channel carried MACRO (rates,
# dollar, FX), so coverage was 5/6 on every candidate. One deterministic item,
# the same for every ticker of a run: US Treasury yields through bond ETF
# proxies (prices move inversely to yields), the dollar index proxy and the
# EUR/USD exchange rate, all from IB Gateway daily bars (read-only).
MACRO_PROXIES = (
    ("US Treasury yields, 7-10y (bond ETF price, inverse to yields)",
     Proxy("IEF", "NASDAQ", "USD", "iShares 7-10 Year Treasury Bond ETF")),
    ("US Treasury yields, 20y+ (bond ETF price, inverse to yields)",
     Proxy("TLT", "NASDAQ", "USD", "iShares 20+ Year Treasury Bond ETF")),
    ("US dollar index", Proxy("UUP", "ARCA", "USD", "Invesco DB US Dollar Index Bullish Fund")),
)
_ALT_PRIMARY = {"NASDAQ": ("NASDAQ", "ARCA"), "ARCA": ("ARCA", "NYSE")}


class MacroContextBuilder:
    """One MACRO context text per run (cached); proxies that cannot be read are left out."""

    def __init__(self, session: GatewaySession, *, duration: str = "1 Y") -> None:
        self._session = session
        self._duration = duration
        self._built = False
        self._value: tuple[str, dict] | None = None

    def _proxy_close(self, proxy: Proxy) -> pd.Series | None:
        for primary in _ALT_PRIMARY.get(proxy.primary_exchange, (proxy.primary_exchange,)):
            try:
                conid = self._session.resolve_stock(proxy.symbol, primary, proxy.currency)
                bars = self._session.daily_history(conid, "SMART", duration=self._duration)
            except (BrokerClientError, BrokerNoDataError):
                continue
            return pd.Series([b.close for b in bars], index=[b.day for b in bars], dtype=float).dropna()
        return None

    def _eurusd(self) -> pd.Series | None:
        try:
            bars = self._session.fx_daily_history("USD", duration=self._duration)
        except (BrokerClientError, BrokerNoDataError):
            return None
        return pd.Series([b.close for b in bars], index=[b.day for b in bars], dtype=float).dropna()

    def describe(self) -> tuple[str, dict] | None:
        if self._built:
            return self._value
        self._built = True
        lines, used = [], []
        for label, proxy in MACRO_PROXIES:
            series = self._proxy_close(proxy)
            if series is None or len(series) < 6:
                continue
            text, _ = _describe(label, proxy, series)
            lines.append(text)
            used.append(proxy.symbol)
        fx = self._eurusd()
        if fx is not None and len(fx) >= 6:
            rets = _returns(fx)
            lines.append(f"EUR/USD exchange rate {float(fx.iloc[-1]):.4f} USD per EUR: "
                         + ", ".join(f"{n}-session {r:+.2f}%" for n, r in rets.items()) + ".")
            used.append("EUR.USD")
        if not lines:
            return None
        text = ("Macro context (IB Gateway daily bars; interest rates and currency): " + " ".join(lines))
        self._value = (text, {"proxies": used})
        return self._value
