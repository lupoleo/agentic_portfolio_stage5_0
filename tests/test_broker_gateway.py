"""IB Gateway history fallback and news provider, on behaviour recorded in the S5.0-1 spike (2026-11-06)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.ai.evidence_provider import EvidenceFetchStatus, EvidenceKind, EvidenceProviderConnectionError, EvidenceRequest
from app.ai.ibkr_news_evidence_provider import IbkrGatewayNewsEvidenceProvider
from app.broker.gateway import (
    BrokerPermissionError,
    ContractInfo,
    DailyBar,
    Headline,
    clean_headline,
    history_with_listing_fallback,
)
from app.broker.ibkr_client import BrokerClientError, BrokerNoDataError

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
BAR = DailyBar(date(2026, 11, 5), 1, 2, 0.5, 1.5, 100)

INFO = {
    175394979: ContractInfo(175394979, "SGLD", "BVME.ETF", "BVME.ETF", "EUR", "INVESCO PHYSICAL GOLD ETC", "STK"),
    68490696: ContractInfo(68490696, "SGLD", "LSEETF", "LSEETF", "USD", "INVESCO PHYSICAL GOLD ETC", "STK"),
    111: ContractInfo(111, "8PSG", "IBIS2", "IBIS2", "EUR", "INVESCO PHYSICAL GOLD ETC", "STK"),
    211008268: ContractInfo(211008268, "PST", "BVME", "BVME", "EUR", "POSTE ITALIANE SPA", "STK"),
}


class FakeSession:
    def __init__(self, history=None, headlines=None, providers=("BRFG", "DJ-N")):
        self.history = history or {}
        self._headlines = headlines or {}
        self.providers = list(providers)
        self.calls = []

    def contract_info(self, conid, exchange=""):
        return INFO[conid]

    def daily_history(self, conid, exchange, *, duration="1 Y"):
        self.calls.append(("history", conid, exchange))
        answer = self.history.get(conid, [BAR])
        if isinstance(answer, Exception):
            raise answer
        return answer

    def news_providers(self):
        self.calls.append(("providers",))
        return self.providers

    def headlines(self, conid, provider_codes, *, limit=20):
        self.calls.append(("headlines", conid, tuple(provider_codes), limit))
        answer = self._headlines.get(conid, [])
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_held_listing_history_is_used_when_permitted():
    result = history_with_listing_fallback(FakeSession(), 211008268, "BVME")
    assert result.listing.conid == 211008268 and result.substituted_for is None and result.bars == [BAR]


def test_milan_etf_without_permission_uses_same_currency_listing():
    session = FakeSession(history={175394979: BrokerPermissionError("IB error 162: No market data permissions for BVME.ETF")})
    result = history_with_listing_fallback(session, 175394979, "BVME.ETF", (68490696, 111))
    assert result.listing.conid == 111 and result.substituted_for == 175394979
    assert any("skipped: USD/STK" in n for n in result.notes)  # the London USD line is never used for a EUR holding
    assert result.notes[-1].startswith("history from 8PSG@IBIS2")


def test_no_permitted_listing_fails_closed():
    session = FakeSession(history={175394979: BrokerPermissionError("162")})
    with pytest.raises(BrokerPermissionError):
        history_with_listing_fallback(session, 175394979, "BVME.ETF", (68490696,))


def test_other_errors_are_not_masked_by_the_fallback():
    session = FakeSession(history={211008268: BrokerClientError("connection lost")})
    with pytest.raises(BrokerClientError):
        history_with_listing_fallback(session, 211008268, "BVME", (111,))


def test_headline_prefix_is_stripped():
    assert clean_headline("{A:800015:L:en}Moody's Ratings Affirms Poste Italiane's Baa2 Ratings") == (
        "Moody's Ratings Affirms Poste Italiane's Baa2 Ratings"
    )
    assert clean_headline("{A:800008,800015:L:Chinese,en}The Stocks That Most Fear a Data-Center Freeze -- WSJ").startswith("The Stocks")


PST_NEWS = [
    Headline(datetime(2026, 10, 9, 17, 2), "DJ-N", "DJ-N$1", "Moody's Ratings Affirms Poste Italiane's Baa2 Ratings; Outlook Stable"),
    Headline(datetime(2026, 10, 8, 9, 0), "DJ-N", "DJ-N$2", "Telecom Italia Executives Tender Shares into Poste Italiane Offer"),
    Headline(datetime(2026, 10, 8, 9, 1), "DJ-RT", "DJ-RT$3", "telecom italia executives tender shares into poste italiane offer"),
]


def news_provider(session):
    return IbkrGatewayNewsEvidenceProvider(session, clock=lambda: NOW)


def test_headlines_become_deduplicated_news_evidence():
    session = FakeSession(headlines={211008268: PST_NEWS})
    result = news_provider(session).fetch(EvidenceRequest(ticker="PST", metadata={"conid": 211008268}))
    assert result.status is EvidenceFetchStatus.SUCCESS
    assert len(result.items) == 2 and all(i.kind is EvidenceKind.NEWS for i in result.items)
    first = result.items[0]
    assert first.source.source_id == "ibkr-news:DJ-N:DJ-N$1"
    assert first.evidence.published_at == datetime(2026, 10, 9, 17, 2, tzinfo=timezone.utc)
    assert result.metadata["providers"] == ["BRFG", "DJ-N"]
    assert ("headlines", 211008268, ("BRFG", "DJ-N"), 20) in session.calls


def test_funds_get_no_headline_step():
    session = FakeSession(headlines={175394979: PST_NEWS})
    result = news_provider(session).fetch(EvidenceRequest(ticker="SGLD", metadata={"conid": 175394979, "asset_kind": "ETC"}))
    assert result.status is EvidenceFetchStatus.NO_DATA and session.calls == []


@pytest.mark.parametrize("answer", [[], BrokerNoDataError("none")])
def test_no_headline_is_no_data(answer):
    result = news_provider(FakeSession(headlines={199482900: answer})).fetch(EvidenceRequest(ticker="LUVE", metadata={"conid": 199482900}))
    assert result.status is EvidenceFetchStatus.NO_DATA


def test_gateway_failure_is_a_provider_error_so_the_secondary_can_run():
    session = FakeSession(headlines={211008268: BrokerClientError("news 211008268: timeout")})
    with pytest.raises(EvidenceProviderConnectionError):
        news_provider(session).fetch(EvidenceRequest(ticker="PST", metadata={"conid": 211008268}))


def test_session_exposes_no_order_methods():
    from app.broker.gateway import IbAsyncGatewaySession

    public = {n for n in dir(IbAsyncGatewaySession) if not n.startswith("_")}
    assert public == {"close", "contract_info", "daily_history", "news_providers", "headlines",
                      "scanner", "fx_daily_history", "resolve_stock"}  # read-only market scanners and FX bars (S5.0-5)


def test_scanner_cancel_acknowledgements_are_not_logged():
    import logging

    from app.broker.gateway import _ScannerCancelFilter

    def record(message):
        return logging.LogRecord("ib_async.wrapper", logging.ERROR, __file__, 1, message, None, None)

    f = _ScannerCancelFilter()
    assert not f.filter(record("Error 162, reqId 41: Historical Market Data Service error message:"
                               "API scanner subscription cancelled: 41"))
    assert f.filter(record("Error 162, reqId 7: Historical Market Data Service error message:No market data permissions"))
