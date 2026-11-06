"""ISIN-keyed contract resolution on the IBKR connector (architecture §5D.3).

Instruments are identified by ISIN plus a preferred exchange, never by a
broker-specific ticker: Fineco tickers such as EUNL, VDIV or CSSPX are not
IBKR symbols, and a name search can miss (Poste Italiane) or return dozens of
unrelated rows. ``search_contracts`` accepts an ISIN and returns the listings
of that instrument; the resolver picks one deterministically:

1. a listing on the preferred exchange (the venue actually held), if any;
2. otherwise a listing in the ISIN's country (the primary listing for most
   stocks);
3. otherwise the first listing IBKR returns, recorded as such.

If the ISIN search returns nothing, a symbol search is tried only when the
caller supplies both a symbol and an exchange, and only an exact match on both
is accepted. Anything else is ``None``: an unresolved instrument stays
unresolved, it is never guessed.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import Field

from .ibkr_client import ReadOnlyIbkrClient
from .models import BrokerModel

ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# Venue names used by other brokers/exports → IBKR exchange codes.
EXCHANGE_ALIASES: dict[str, frozenset[str]] = {
    "AFF": frozenset({"BVME", "BVME.ETF"}),
    "MTA": frozenset({"BVME"}),
    "ETFPLUS": frozenset({"BVME.ETF"}),
    "MILAN": frozenset({"BVME", "BVME.ETF"}),
    "XETRA": frozenset({"IBIS", "IBIS2"}),
    "FRANKFURT": frozenset({"FWB", "FWB2"}),
    "NASDAQ": frozenset({"NASDAQ"}),
    "NYSE": frozenset({"NYSE"}),
    "AMEX": frozenset({"AMEX"}),
    "ARCA": frozenset({"ARCA"}),
    "LSE": frozenset({"LSE", "LSEETF"}),
    "AMSTERDAM": frozenset({"AEB"}),
    "SIX": frozenset({"EBS"}),
    "PARIS": frozenset({"SBF"}),
}


class ResolutionMethod(str, Enum):
    ISIN_PREFERRED_EXCHANGE = "ISIN_PREFERRED_EXCHANGE"
    ISIN_HOME_COUNTRY = "ISIN_HOME_COUNTRY"
    ISIN_FIRST_LISTING = "ISIN_FIRST_LISTING"
    SYMBOL_AND_EXCHANGE = "SYMBOL_AND_EXCHANGE"


class ResolvedContract(BrokerModel):
    conid: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    description: str | None = None
    country_code: str | None = None
    isin: str | None = None
    security_types: tuple[str, ...] = ()
    method: ResolutionMethod
    alternatives: tuple[int, ...] = ()  # other conids of the same ISIN, for audit


def ibkr_exchanges(exchange: str | None) -> frozenset[str]:
    if not exchange:
        return frozenset()
    key = exchange.strip().upper()
    return EXCHANGE_ALIASES.get(key, frozenset({key}))


class ContractResolver:
    def __init__(self, client: ReadOnlyIbkrClient) -> None:
        self._client = client
        self._cache: dict[tuple[str, str, str], ResolvedContract | None] = {}

    def resolve(
        self,
        isin: str | None,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
    ) -> ResolvedContract | None:
        isin_key = (isin or "").strip().upper()
        cache_key = (isin_key, (exchange or "").upper(), (symbol or "").upper())
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = None
        if isin_key:
            if not ISIN_PATTERN.match(isin_key):
                raise ValueError(f"invalid ISIN {isin!r}")
            result = self._by_isin(isin_key, exchange)
        if result is None and symbol and exchange:
            result = self._by_symbol(symbol.strip().upper(), exchange, isin_key or None)
        self._cache[cache_key] = result
        return result

    def _by_isin(self, isin: str, exchange: str | None) -> ResolvedContract | None:
        rows = _stock_rows(self._client.call("search_contracts", {"query": isin}))
        if not rows:
            return None
        wanted = ibkr_exchanges(exchange)
        preferred = [r for r in rows if r["exchange"].upper() in wanted]
        if preferred:
            chosen, method = preferred[0], ResolutionMethod.ISIN_PREFERRED_EXCHANGE
        else:
            home = [r for r in rows if (r.get("country_code") or "").upper() == isin[:2]]
            if home:
                chosen, method = home[0], ResolutionMethod.ISIN_HOME_COUNTRY
            else:
                chosen, method = rows[0], ResolutionMethod.ISIN_FIRST_LISTING
        return _contract(chosen, method, isin, rows)

    def _by_symbol(self, symbol: str, exchange: str, isin: str | None) -> ResolvedContract | None:
        wanted = ibkr_exchanges(exchange)
        rows = _stock_rows(self._client.call("search_contracts", {"query": symbol}))
        matches = [r for r in rows if r["symbol"].upper() == symbol and r["exchange"].upper() in wanted]
        if len(matches) != 1:
            return None
        return _contract(matches[0], ResolutionMethod.SYMBOL_AND_EXCHANGE, isin, matches)


def _stock_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in result.get("results") or []:
        conid = row.get("underlying_contract_id")
        types = {s.get("security_type") for s in row.get("sections") or [] if isinstance(s, dict)}
        if isinstance(conid, int) and conid > 0 and "STK" in types and row.get("symbol") and row.get("exchange"):
            if row["exchange"].upper() in {"CORPACT", "VALUE"}:  # tenders, rights, escrow lines
                continue
            rows.append(row)
    return rows


def _contract(row: dict[str, Any], method: ResolutionMethod, isin: str | None, rows: list[dict[str, Any]]) -> ResolvedContract:
    return ResolvedContract(
        conid=row["underlying_contract_id"],
        symbol=row["symbol"],
        exchange=row["exchange"],
        description=row.get("description"),
        country_code=row.get("country_code"),
        isin=isin,
        security_types=tuple(sorted({s["security_type"] for s in row.get("sections") or [] if s.get("security_type")})),
        method=method,
        alternatives=tuple(r["underlying_contract_id"] for r in rows if r is not row),
    )
