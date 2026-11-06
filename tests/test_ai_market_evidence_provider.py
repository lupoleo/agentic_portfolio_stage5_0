from datetime import datetime
import pandas as pd
from app.ai.evidence_provider import EvidenceFetchStatus, EvidenceRequest
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider

def frame(n=60):
    idx=pd.date_range("2026-06-01",periods=n,freq="B")
    return pd.DataFrame({"Close":[100+i for i in range(n)],"Volume":[1000+i*10 for i in range(n)]},index=idx)

def test_provider_name():
    assert YahooMarketEvidenceProvider(lambda t: frame()).provider_name=="YAHOO_MARKET"

def test_fetch_real_contract_success():
    r=YahooMarketEvidenceProvider(lambda t: frame()).fetch(EvidenceRequest(ticker="path"))
    assert r.status==EvidenceFetchStatus.SUCCESS and r.ticker=="PATH" and len(r.items)==1

def test_evidence_contains_deterministic_market_facts():
    text=YahooMarketEvidenceProvider(lambda t: frame()).fetch(EvidenceRequest(ticker="PATH")).items[0].evidence.text
    for x in ["Latest available close","5-session","20-session","SMA20","SMA50","RSI14","RVOL"]: assert x in text

def test_provenance_is_linked():
    item=YahooMarketEvidenceProvider(lambda t: frame()).fetch(EvidenceRequest(ticker="PATH")).items[0]
    assert item.evidence.metadata["source_id"]==item.source.source_id
    assert item.source.provider=="YAHOO_MARKET"

def test_no_data_is_not_fabricated():
    r=YahooMarketEvidenceProvider(lambda t: pd.DataFrame()).fetch(EvidenceRequest(ticker="PATH"))
    assert r.status==EvidenceFetchStatus.NO_DATA and r.items==[]

def test_short_history_degrades_gracefully():
    r=YahooMarketEvidenceProvider(lambda t: frame(6)).fetch(EvidenceRequest(ticker="PATH"))
    text=r.items[0].evidence.text
    assert "5-session" in text and "SMA20" not in text

def test_ticker_is_normalized_by_request():
    r=YahooMarketEvidenceProvider(lambda t: frame()).fetch(EvidenceRequest(ticker=" path "))
    assert r.ticker=="PATH" and r.items[0].ticker=="PATH"

def test_evidence_ids_are_deterministic_for_same_data():
    p=YahooMarketEvidenceProvider(lambda t: frame())
    a=p.fetch(EvidenceRequest(ticker="PATH")).items[0].evidence.evidence_id
    b=p.fetch(EvidenceRequest(ticker="PATH")).items[0].evidence.evidence_id
    assert a==b
