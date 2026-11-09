"""IBKR evidence channels for the research pipeline (S5.0-4), on a synthetic Gateway recording."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceProvider,
    EvidenceProviderConnectionError,
    EvidenceRequest,
    EvidenceSource,
)
from app.ai.ibkr_evidence_wiring import CompositeEvidenceProvider, WithInstrumentContext, build_ibkr_evidence_channels
from app.ai.ibkr_market import IbkrHistoryLoader, IbkrMarketEvidenceProvider, ibkr_canonical_technical_loader
from app.ai.research_service import ResearchEvidence
from app.broker.gateway import IbAsyncGatewaySession, RecordedGatewaySession
from app.broker.ibkr_client import BrokerClientError, BrokerNoDataError
from app.broker.instrument_directory import InstrumentDirectory, InstrumentRef

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
MSFT, NBIS = 272093, 88819736


def bars(n=260, start=100.0):
    day, out = date(2025, 11, 3), []
    for i in range(n):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        close = start + i * 0.5 + (i % 7) * 0.3
        out.append({"day": day.isoformat(), "open": close - 0.4, "high": close + 1, "low": close - 1, "close": close,
                    "volume": 1000 + 10 * (i % 13)})
        day += timedelta(days=1)
    return out


def contract(conid, symbol):
    return {"conid": conid, "symbol": symbol, "exchange": "NASDAQ", "primary_exchange": "NASDAQ", "currency": "USD",
            "long_name": symbol, "sec_type": "STK"}


RECORDING = {
    "news_providers": ["BRFG", "DJ-N"],
    "instruments": {
        str(MSFT): {
            "contract": contract(MSFT, "MSFT"),
            "bars": bars(),
            "headlines": [
                {"published_at": "2026-11-05T14:00:00", "provider_code": "DJ-N", "article_id": "DJ-N$1",
                 "headline": "Microsoft expands Azure capacity"},
                {"published_at": "2026-11-04T09:00:00", "provider_code": "BRFG", "article_id": "BRFG$2",
                 "headline": "Microsoft upgraded at a broker"},
            ],
        },
        str(NBIS): {"contract": contract(NBIS, "NBIS"), "history_error": "history 88819736: IB error 321",
                    "news_error": "news 88819736: timeout after 3 attempts"},
    },
}


def directory():
    return InstrumentDirectory([
        InstrumentRef(ticker="MSFT", conid=MSFT, isin="US5949181045", currency="USD"),
        InstrumentRef(ticker="NBIS", conid=NBIS, isin="NL0009805522", currency="USD"),
    ])


class Fixed(EvidenceProvider):
    """A stand-in for a Yahoo provider."""

    def __init__(self, name, kind=EvidenceKind.NEWS, error=None):
        self.name, self.kind, self.error, self.calls = name, kind, error, []

    @property
    def provider_name(self):
        return self.name

    def fetch(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        sid = f"{self.name}-{request.ticker}"
        item = EvidenceItem(
            evidence=ResearchEvidence(evidence_id=sid, source_type=self.kind.value, text=f"{self.name} text"),
            source=EvidenceSource(source_id=sid, provider=self.name, source_type=self.kind.value, source_name=self.name,
                                  retrieved_at=NOW),
            kind=self.kind, ticker=request.ticker,
        )
        return EvidenceFetchResult(provider=self.name, ticker=request.ticker, status=EvidenceFetchStatus.SUCCESS,
                                   items=[item], fetched_at=NOW)


# --- recording, directory, session -------------------------------------------------

def test_recorded_session_replays_contract_bars_and_headlines():
    s = RecordedGatewaySession(RECORDING)
    assert s.contract_info(MSFT).symbol == "MSFT"
    assert len(s.daily_history(MSFT, "SMART")) == 260
    assert [h.article_id for h in s.headlines(MSFT, ["DJ-N"], limit=1)] == ["DJ-N$1"]
    assert s.news_providers() == ["BRFG", "DJ-N"]
    with pytest.raises(BrokerClientError):
        s.daily_history(NBIS, "SMART")
    with pytest.raises(BrokerNoDataError):
        s.contract_info(1)


def test_directory_rejects_one_ticker_for_two_instruments():
    d = directory()
    assert d.get(" msft ").conid == MSFT and d.get("AAPL") is None
    with pytest.raises(ValueError):
        d.add(InstrumentRef(ticker="MSFT", conid=1))


class _FlakyNewsIB:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def reqHistoricalNews(self, *args):
        self.calls += 1
        return self.answers.pop(0)


def _session_with(ib):
    session = IbAsyncGatewaySession.__new__(IbAsyncGatewaySession)
    session._ib, session._errors = ib, {}
    return session


def test_news_timeout_is_retried_then_reported():
    ib = _FlakyNewsIB([None, []])
    assert _session_with(ib).headlines(MSFT, ["DJ-N"]) == [] and ib.calls == 2
    ib = _FlakyNewsIB([None, None, None])
    with pytest.raises(BrokerClientError, match="timeout after 3 attempts"):
        _session_with(ib).headlines(MSFT, ["DJ-N"])


# --- market history ------------------------------------------------------------------

def test_history_loader_builds_a_yahoo_shaped_frame_once():
    loader = IbkrHistoryLoader(RecordedGatewaySession(RECORDING), directory())
    frame = loader("msft")
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(frame.index, pd.DatetimeIndex) and frame.index.name == "Date" and len(frame) == 260
    assert loader("MSFT") is frame
    with pytest.raises(BrokerNoDataError):
        loader("AAPL")


def test_market_evidence_and_technical_input_come_from_ibkr_history():
    loader = IbkrHistoryLoader(RecordedGatewaySession(RECORDING), directory())
    result = IbkrMarketEvidenceProvider(loader).fetch(EvidenceRequest(ticker="MSFT"))
    (item,) = result.items
    assert result.provider == "IBKR_MARKET" and item.kind is EvidenceKind.MARKET
    assert item.source.source_id.startswith("SRC-IBKR-MSFT") and item.evidence.evidence_id.startswith("EVID-IBKR-MSFT")
    assert item.metadata["source"] == "IBKR" and item.source.source_url is None
    assert "SMA50" in item.evidence.text
    technical = ibkr_canonical_technical_loader(loader)("MSFT")
    assert technical.ticker == "MSFT" and technical.current_price == pytest.approx(loader("MSFT")["Close"].iloc[-1])


# --- context and composite --------------------------------------------------------

def test_context_adds_identity_and_unknown_ticker_is_no_data():
    inner = Fixed("IBKR_X")
    wrapped = WithInstrumentContext(inner, directory(), clock=lambda: NOW)
    wrapped.fetch(EvidenceRequest(ticker="MSFT", metadata={"hypothesis_id": "h1"}))
    assert inner.calls[0].metadata["conid"] == MSFT and inner.calls[0].metadata["hypothesis_id"] == "h1"
    result = wrapped.fetch(EvidenceRequest(ticker="AAPL"))
    assert result.status is EvidenceFetchStatus.NO_DATA and len(inner.calls) == 1


def test_composite_merges_and_reports_partial_or_raises():
    req = EvidenceRequest(ticker="MSFT")
    ok = CompositeEvidenceProvider("C", [Fixed("A"), Fixed("B")], clock=lambda: NOW).fetch(req)
    assert ok.status is EvidenceFetchStatus.SUCCESS and len(ok.items) == 2
    err = EvidenceProviderConnectionError("down")
    partial = CompositeEvidenceProvider("C", [Fixed("A"), Fixed("B", error=err)], clock=lambda: NOW).fetch(req)
    assert partial.status is EvidenceFetchStatus.PARTIAL and "B failed" in partial.warnings[0]
    with pytest.raises(EvidenceProviderConnectionError):
        CompositeEvidenceProvider("C", [Fixed("B", error=err)], clock=lambda: NOW).fetch(req)


# --- whole channel set ------------------------------------------------------------

def channels():
    yahoo = {k: Fixed(f"YAHOO_{k.upper()}", kind) for k, kind in [
        ("market", EvidenceKind.MARKET), ("news", EvidenceKind.NEWS),
        ("fundamental", EvidenceKind.FUNDAMENTAL), ("analyst", EvidenceKind.ANALYST),
    ]}
    yahoo_technical = []
    built = build_ibkr_evidence_channels(
        RecordedGatewaySession(RECORDING), directory(),
        secondary_market=yahoo["market"], secondary_news=yahoo["news"],
        secondary_fundamental=yahoo["fundamental"], secondary_analyst=yahoo["analyst"],
        secondary_technical_loader=lambda t: yahoo_technical.append(t) or "yahoo-technical",
        clock=lambda: NOW,
    )
    return built, yahoo, yahoo_technical


def test_held_us_stock_uses_ibkr_only():
    built, yahoo, yahoo_technical = channels()
    req = EvidenceRequest(ticker="MSFT", max_items=10)
    news = built.news.fetch(req)
    assert [i.source.source_id for i in news.items] == ["ibkr-news:DJ-N:DJ-N$1", "ibkr-news:BRFG:BRFG$2"]
    assert built.market.fetch(req).provider == "IBKR_MARKET"
    assert built.canonical_technical_loader("MSFT").ticker == "MSFT"
    assert yahoo["market"].calls == [] and yahoo["news"].calls == [] and yahoo_technical == []


def test_ibkr_failures_fall_back_to_marked_yahoo():
    built, yahoo, yahoo_technical = channels()
    req = EvidenceRequest(ticker="NBIS")
    news = built.news.fetch(req)  # Gateway news timed out
    assert news.items[0].metadata["secondary_source"] is True and news.items[0].metadata["primary_provider"] == "IBKR_NEWS_CHANNEL"
    market = built.market.fetch(req)  # Gateway history failed
    assert market.provider == "YAHOO_MARKET" and market.items[0].metadata["secondary_source"] is True
    assert built.canonical_technical_loader("NBIS") == "yahoo-technical" and built.technical_secondary_tickers == ["NBIS"]


def test_unresolved_candidate_and_analyst_channels_are_declared_secondary():
    built, yahoo, _ = channels()
    req = EvidenceRequest(ticker="AAPL")
    assert built.news.fetch(req).items[0].metadata["secondary_source"] is True
    assert built.market.fetch(req).items[0].metadata["secondary_source"] is True
    for channel in (built.fundamental, built.analyst):
        item = channel.fetch(EvidenceRequest(ticker="MSFT")).items[0]
        assert item.metadata["secondary_source"] is True and "§5D.2" in item.metadata["primary_reason"]


def test_without_secondary_an_ibkr_failure_stays_an_error():
    built = build_ibkr_evidence_channels(RecordedGatewaySession(RECORDING), directory(), clock=lambda: NOW)
    with pytest.raises(EvidenceProviderConnectionError):
        built.news.fetch(EvidenceRequest(ticker="NBIS"))
    assert built.fundamental is None and built.analyst is None


# --- runtime configuration ------------------------------------------------------

def runtime_configuration(tmp_path, **updates):
    from app.e2e.stage4_runtime import Stage4RuntimeConfiguration

    values = dict(
        database_path=str(tmp_path / "db.sqlite"), portfolio_snapshot_id="s", portfolio_risk_state_id="r",
        eligibility_report_path="e.json", mapping_report_path="m.json", history_report_paths=("h.json",),
    )
    values.update(updates)
    return Stage4RuntimeConfiguration(**values)


def test_runtime_defaults_to_4_0_sources_and_validates_the_choice(tmp_path):
    assert runtime_configuration(tmp_path).evidence_source == "yahoo"
    with pytest.raises(ValueError):
        runtime_configuration(tmp_path, evidence_source="bloomberg")


def test_runtime_builds_ibkr_channels_from_recording_and_directory(tmp_path):
    import json

    from app.broker.instrument_directory import InstrumentDirectory, InstrumentRef
    from app.e2e.stage4_runtime import CanonicalStage4Runtime

    (tmp_path / "rec.json").write_text(json.dumps(RECORDING), encoding="utf-8")
    config = runtime_configuration(tmp_path, evidence_source="IBKR", ibkr_gateway_recording_path=str(tmp_path / "rec.json"))
    directory = InstrumentDirectory([InstrumentRef(ticker="MSFT", conid=MSFT, isin="US5949181045", currency="USD")])
    runtime = CanonicalStage4Runtime(config, instrument_directory=directory)
    gateway, built = runtime._ibkr_channels(Fixed("YAHOO_MARKET", EvidenceKind.MARKET), Fixed("YAHOO_NEWS"), None, None, None)
    assert isinstance(gateway, RecordedGatewaySession)
    assert built.news.fetch(EvidenceRequest(ticker="MSFT")).items[0].source.provider == "IBKR_GATEWAY_NEWS"
    assert built.fundamental is None


def test_runtime_ibkr_needs_an_instrument_directory(tmp_path):
    from app.e2e.stage4_runtime import CanonicalStage4Runtime

    runtime = CanonicalStage4Runtime(runtime_configuration(tmp_path, evidence_source="ibkr"))
    with pytest.raises(ValueError, match="instrument directory"):
        runtime._ibkr_channels(None, None, None, None, None)


def test_newest_headlines_carry_the_article_text():
    from app.ai.ibkr_news_evidence_provider import IbkrGatewayNewsEvidenceProvider
    from app.broker.gateway import article_text

    recording = {**RECORDING, "articles": {
        "DJ-N$1": "<p>Microsoft said Azure capacity will grow 40% in fiscal 2027 &amp; capex rises.</p>" + " word" * 600,
    }}
    provider = IbkrGatewayNewsEvidenceProvider(RecordedGatewaySession(recording), clock=lambda: NOW)
    first, second = provider.fetch(EvidenceRequest(ticker="MSFT", metadata={"conid": MSFT}, max_items=10)).items
    assert first.evidence.text.startswith("Microsoft expands Azure capacity\n\nMicrosoft said Azure capacity will grow 40%")
    assert "&amp;" not in first.evidence.text and "<p>" not in first.evidence.text
    assert first.evidence.text.endswith("[...]") and len(first.evidence.text) < 1600
    assert first.metadata["article_body"] is True
    assert second.evidence.text == "Microsoft upgraded at a broker"  # no body recorded: headline only
    assert article_text("<b>A</b>\n\n B &lt;C&gt;") == "A B <C>"
