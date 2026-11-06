"""ISIN-keyed contract resolution, on responses recorded from the IBKR connector (2026-11-06)."""

from __future__ import annotations

import pytest

from app.broker.contract_resolver import ContractResolver, ResolutionMethod, ibkr_exchanges
from app.broker.ibkr_client import ReadOnlyIbkrClient


def row(conid, exchange, symbol, description, country=None, types=("STK",)):
    out = {
        "underlying_contract_id": conid,
        "exchange": exchange,
        "symbol": symbol,
        "description": description,
        "sections": [{"security_type": t} for t in types],
    }
    if country:
        out["country_code"] = country
    return {k: v for k, v in out.items()}


SEARCH = {
    # Invesco Physical Gold ETC, held on Borsa Italiana (Fineco venue "AFF")
    "IE00B579F325": [
        row(68490696, "LSEETF", "SGLD", "INVESCO PHYSICAL GOLD ETC", "GB", ("STK", "CFD")),
        row(175394979, "BVME.ETF", "SGLD", "INVESCO PHYSICAL GOLD ETC", "IT"),
        row(858451238, "EBS", "SGLD", "INVESCO PHYSICAL GOLD ETC", "CH"),
    ],
    # iShares Core MSCI World: Fineco "EUNL" on XETRA is not offered; Amsterdam first
    "IE00B4L5Y983": [
        row(100292038, "AEB", "IWDA", "ISHARES CORE MSCI WORLD", "NL", ("STK", "OPT")),
        row(78999785, "LSEETF", "IWDA", "ISHARES CORE MSCI WORLD", "GB"),
        row(69169660, "LSEETF", "SWDA", "ISHARES CORE MSCI WORLD", "GB"),
    ],
    # Unipol: the ISIN search is assumed here to return the home listing plus an ADR and a tender line
    "IT0004810054": [
        row(105804878, "PINK", "UFGSY", "UNIPOL GRUPPO-UNSPON ADR", "US"),
        row(938405788, "CORPACT", "UNI.RTS2", "UNIPOL ASSICURAZIONI SPA - RIGHTS SUBSCRIPTION"),
        row(105387889, "BVME", "UNI", "UNIPOL ASSICURAZIONI SPA", "IT", ("STK", "OPT", "FUT")),
    ],
    # Poste Italiane: no hit by ISIN or by name in the recorded session, only by symbol
    "IT0003796171": [],
    "PST": [
        row(211008268, "BVME", "PST", "POSTE ITALIANE SPA", "IT", ("STK", "OPT")),
        row(50918095, "ARCA", "PST", "PROSHARES ULTRASHORT 7-10 YR", "US"),
        row(911185088, "PINK", "PSTEY", "POSTE ITALIAN SPA-UNSPON ADR", "US"),
    ],
    "CSSPX": [row(141450732, "FUNDSERV", "CSSPX", "COHEN & STEERS GLOBAL REALTY", "US", ("FUND",))],
}


class QueryTransport:
    def __init__(self):
        self.queries = []

    def call(self, tool, arguments):
        assert tool == "search_contracts"
        self.queries.append(arguments["query"])
        return {"results": SEARCH.get(arguments["query"], [])}


@pytest.fixture
def resolver():
    transport = QueryTransport()
    r = ContractResolver(ReadOnlyIbkrClient(transport))
    r.transport = transport
    return r


def test_preferred_exchange_wins_over_first_listing(resolver):
    got = resolver.resolve("IE00B579F325", exchange="AFF")
    assert (got.conid, got.exchange, got.method) == (175394979, "BVME.ETF", ResolutionMethod.ISIN_PREFERRED_EXCHANGE)
    assert set(got.alternatives) == {68490696, 858451238}


def test_home_country_listing_when_venue_not_offered(resolver):
    got = resolver.resolve("IT0004810054", exchange="NYSE")
    assert (got.conid, got.symbol, got.method) == (105387889, "UNI", ResolutionMethod.ISIN_HOME_COUNTRY)
    assert 938405788 not in got.alternatives  # corporate-action lines are never candidates


def test_first_listing_is_recorded_as_such(resolver):
    got = resolver.resolve("IE00B4L5Y983", exchange="XETRA")
    assert (got.conid, got.symbol, got.method) == (100292038, "IWDA", ResolutionMethod.ISIN_FIRST_LISTING)


def test_symbol_fallback_needs_exact_symbol_and_exchange(resolver):
    got = resolver.resolve("IT0003796171", exchange="AFF", symbol="PST")
    assert (got.conid, got.method, got.isin) == (211008268, ResolutionMethod.SYMBOL_AND_EXCHANGE, "IT0003796171")
    assert resolver.resolve("IT0003796171", exchange="NYSE", symbol="PST") is None
    assert resolver.resolve("IT0003796171", symbol="PST") is None


def test_broker_tickers_that_are_not_ibkr_symbols_stay_unresolved(resolver):
    # CSSPX on IBKR is an unrelated US mutual fund: never accepted as the iShares S&P 500 ETF
    assert resolver.resolve(None, exchange="AFF", symbol="CSSPX") is None


def test_results_are_cached_per_isin_and_venue(resolver):
    resolver.resolve("IE00B579F325", exchange="AFF")
    resolver.resolve("IE00B579F325", exchange="AFF")
    assert resolver.transport.queries == ["IE00B579F325"]


def test_invalid_isin_is_rejected(resolver):
    with pytest.raises(ValueError):
        resolver.resolve("NOT-AN-ISIN")


def test_exchange_aliases():
    assert ibkr_exchanges("aff") == {"BVME", "BVME.ETF"}
    assert ibkr_exchanges("XETRA") == {"IBIS", "IBIS2"}
    assert ibkr_exchanges("EBS") == {"EBS"}
    assert ibkr_exchanges(None) == frozenset()
