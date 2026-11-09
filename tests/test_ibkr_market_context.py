"""Market and sector context evidence from IB Gateway bars of ETF proxies."""

from __future__ import annotations

from app.ai.evidence_provider import EvidenceKind, EvidenceRequest
from app.ai.ibkr_market import IbkrHistoryLoader, IbkrMarketEvidenceProvider
from app.ai.ibkr_market_context import MarketContextBuilder, sector_bucket
from app.broker.gateway import RecordedGatewaySession
from app.broker.instrument_directory import InstrumentDirectory, InstrumentRef
from tests.test_ibkr_evidence_wiring import bars


def contract(conid, symbol, exchange, currency, **extra):
    return {"conid": conid, "symbol": symbol, "exchange": "SMART", "primary_exchange": exchange,
            "currency": currency, "long_name": symbol, "sec_type": "STK", **extra}


RECORDING = {"instruments": {
    "1": {"contract": contract(1, "ISP", "BVME", "EUR", industry="Financial", category="Banks"), "bars": bars(start=100)},
    "2": {"contract": contract(2, "EXSA", "IBIS", "EUR"), "bars": bars(start=50)},
    "3": {"contract": contract(3, "EXV1", "IBIS", "EUR"), "bars": bars(start=20)},
    "4": {"contract": contract(4, "KPN", "AEB", "EUR", industry="Communications", category="Telecommunications"),
          "bars": bars(start=3)},
}}


def directory():
    return InstrumentDirectory([InstrumentRef(ticker="ISP.MI", conid=1, currency="EUR"),
                                InstrumentRef(ticker="KPN.AS", conid=4, currency="EUR")])


def provider():
    session = RecordedGatewaySession(RECORDING)
    loader = IbkrHistoryLoader(session, directory())
    return IbkrMarketEvidenceProvider(loader, MarketContextBuilder(session, directory()))


def test_sector_buckets_from_ibkr_industry_and_category():
    assert sector_bucket("Financial", "Banks") == "banks"
    assert sector_bucket("Financial", "Insurance") == "insurance"
    assert sector_bucket("Communications", "Telecommunications") == "telecom"
    assert sector_bucket("Consumer, Non-cyclical", "Pharmaceuticals") == "health_care"
    assert sector_bucket("Energy", "Oil&Gas") == "energy"
    assert sector_bucket("", "") is None


def test_context_item_reports_benchmark_sector_and_relative_returns():
    result = provider().fetch(EvidenceRequest(ticker="ISP.MI"))
    market, context = result.items
    assert context.kind is EvidenceKind.MARKET and context.evidence.evidence_id.startswith("EVID-IBKRCTX-ISP.MI")
    text = context.evidence.text
    assert "STOXX Europe 600" in text and "(EXSA)" in text and "(EXV1)" in text
    assert "20-session" in text and "Relative performance of ISP.MI: vs EXSA:" in text and "pp" in text
    assert context.source.metadata["sector_bucket"] == "banks" and context.source.metadata["proxies"] == ["EXSA", "EXV1"]
    assert market.evidence.evidence_id.startswith("EVID-IBKR-ISP.MI")


def test_missing_sector_proxy_keeps_the_benchmark_only():
    (_, context) = provider().fetch(EvidenceRequest(ticker="KPN.AS")).items  # EXV2 is not available
    assert "(EXSA)" in context.evidence.text and "EXV2" not in context.evidence.text
    assert context.source.metadata["proxies"] == ["EXSA"]


def test_macro_item_carries_rates_dollar_and_eurusd_and_is_tagged_macro():
    from app.ai.evidence_semantics import EvidenceSemanticDimension, deterministic_semantic_dimensions
    from app.ai.ibkr_market_context import MacroContextBuilder

    recording = {"instruments": {
        **RECORDING["instruments"],
        "10": {"contract": contract(10, "IEF", "NASDAQ", "USD"), "bars": bars(start=95)},
        "11": {"contract": contract(11, "UUP", "ARCA", "USD"), "bars": bars(start=28)},
    }, "fx": {"USD": [{"day": b["day"], "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1 + i * 0.001}
                      for i, b in enumerate(bars(start=1))]}}
    session = RecordedGatewaySession(recording)
    macro = MacroContextBuilder(session)
    p = IbkrMarketEvidenceProvider(IbkrHistoryLoader(session, directory()), MarketContextBuilder(session, directory()), macro)
    items = p.fetch(EvidenceRequest(ticker="ISP.MI")).items
    item = items[-1]
    assert item.evidence.evidence_id.startswith("EVID-IBKRMACRO-ISP.MI") and item.kind is EvidenceKind.MARKET
    text = item.evidence.text
    assert "(IEF)" in text and "(UUP)" in text and "EUR/USD exchange rate" in text and "TLT" not in text
    assert item.source.metadata["proxies"] == ["IEF", "UUP", "EUR.USD"]
    assert EvidenceSemanticDimension.MACRO in deterministic_semantic_dimensions(text)
    assert macro.describe() is macro.describe()  # built once per run


def test_no_macro_item_when_no_proxy_is_readable():
    from app.ai.ibkr_market_context import MacroContextBuilder

    session = RecordedGatewaySession(RECORDING)
    p = IbkrMarketEvidenceProvider(IbkrHistoryLoader(session, directory()), None, MacroContextBuilder(session))
    assert len(p.fetch(EvidenceRequest(ticker="ISP.MI")).items) == 1
