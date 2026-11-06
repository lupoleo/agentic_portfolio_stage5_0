"""AI-8C.2-R4: Yahoo news through the ticker feed, symbol search and name search."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

import pytest

from app.ai.evidence_provider import EvidenceFetchStatus, EvidenceProviderResponseError, EvidenceRequest
from app.ai.news_evidence_provider import (
    NEWS_SELECTION_POLICY_VERSION,
    YahooNewsEvidenceProvider,
    mentions,
    normalize_company_name,
)
from app.e2e.stage4_frontier_priority import aliases_from_eligibility


AS_OF = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def flat(title, *, hours=2, publisher="Wire", link=None):
    published = AS_OF - timedelta(hours=hours)
    return {
        "uuid": title, "title": title, "publisher": publisher,
        "link": link or f"https://example.com/{abs(hash(title))}",
        "providerPublishTime": int(published.timestamp()),
    }


def failing_feed(ticker):
    # yfinance 1.6/1.7 behaviour observed live on 2026-10-04/05.
    logging.getLogger("yfinance").error("%s: Failed to retrieve the news and received faulty response instead.", ticker)
    return []


class Search:
    def __init__(self, responses, fail=()):
        self.responses = responses
        self.fail = set(fail)
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        if query in self.fail:
            raise ConnectionError("search down")
        return self.responses.get(query, {"news": [], "quotes": []})


def fetch(provider, ticker, **metadata):
    return provider.fetch(EvidenceRequest(ticker=ticker, as_of=AS_OF, metadata=metadata))


@pytest.mark.parametrize("raw, expected", [
    ("UniCredit S.p.A.", "UniCredit"),
    ("Banco Bpm", "Banco Bpm"),
    ("Bayerische Motoren Werke Aktiengesellschaft", "Bayerische Motoren Werke"),
    ("Drägerwerk AG & Co. KGaA (pref)", "Drägerwerk"),
    ("SAP SE ADR", "SAP"),
    ("Apple Inc.", "Apple"),
    ("Raymond James Financial Inc.", "Raymond James Financial"),
    ("AG", None),
    (None, None),
    ("SAP SE                        I", "SAP"),
    ("BAYERISCHE MOTOREN WERKE AG   S", "Bayerische Motoren Werke"),
    ("UNICREDIT", "Unicredit"),
    ("BANCO BPM", "Banco BPM"),
])
def test_normalize_company_name(raw, expected):
    assert normalize_company_name(raw) == expected


def test_mentions_uses_whole_words():
    assert mentions("Eni CEO meets Argentine President", ["Eni"])
    assert not mentions("Senior notes priced for a utility", ["Eni"])
    assert mentions("Synthesized now available on SAP Store", ["SAP"])
    assert not mentions("Where Will Nvidia Stock Be in 2030?", ["AAPL", "Apple"])


def test_working_ticker_feed_is_used_alone():
    search = Search({})
    provider = YahooNewsEvidenceProvider(lambda t: [flat("Apple unveils new chip")], search_loader=search)
    result = fetch(provider, "AAPL")
    assert result.status is EvidenceFetchStatus.SUCCESS
    assert search.queries == []
    assert result.items[0].evidence.metadata["news_channel"] == "TICKER_FEED"
    assert result.metadata["selection_policy_version"] == NEWS_SELECTION_POLICY_VERSION


def test_failed_feed_falls_back_to_symbol_search_with_relevance_filter():
    search = Search({"AAPL": {
        "news": [flat("Where Will Nvidia Stock Be in 2030?"), flat("Apple's biggest AI advantage may face a challenge")],
        "quotes": [{"symbol": "AAPL", "shortname": "Apple Inc.", "longname": "Apple Inc."}],
    }})
    result = fetch(YahooNewsEvidenceProvider(failing_feed, search_loader=search), "AAPL")
    assert result.status is EvidenceFetchStatus.SUCCESS
    assert [item.metadata["headline"] for item in result.items] == [
        "Apple's biggest AI advantage may face a challenge"
    ]
    assert result.items[0].evidence.metadata["news_channel"] == "SEARCH_SYMBOL"
    assert result.metadata["news_channels"]["TICKER_FEED"].startswith("FAILED")
    assert search.queries == ["AAPL"]


def test_european_listing_uses_name_from_quotes():
    search = Search({
        "UCG.MI": {"news": [], "quotes": [{"symbol": "UCG.MI", "shortname": "UNICREDIT", "longname": "UniCredit S.p.A."}]},
        "Unicredit": {"news": [
            flat("RBC downgrades Commerzbank on rising execution risk from UniCredit plan"),
            flat("European banks rally on rate outlook"),
        ], "quotes": []},
    })
    result = fetch(YahooNewsEvidenceProvider(failing_feed, search_loader=search), "UCG.MI")
    assert [item.metadata["headline"] for item in result.items] == [
        "RBC downgrades Commerzbank on rising execution risk from UniCredit plan"
    ]
    assert result.items[0].evidence.metadata["news_channel"] == "SEARCH_NAME"
    assert search.queries == ["UCG.MI", "Unicredit"]


def test_request_company_names_take_precedence():
    search = Search({"BAMI.MI": {"news": [], "quotes": []}, "Banco Bpm": {"news": [
        flat("Monte Paschi launches EUR34bn bids for Banco BPM and Banca Generali"),
    ], "quotes": []}})
    result = fetch(YahooNewsEvidenceProvider(failing_feed, search_loader=search), "BAMI.MI",
                   company_names=["Banco Bpm"])
    assert len(result.items) == 1
    assert search.queries == ["BAMI.MI", "Banco Bpm"]


def test_all_channels_down_is_a_provider_failure_not_no_news():
    search = Search({}, fail={"AAPL", "Apple"})

    def broken_feed(ticker):
        raise TimeoutError("feed timeout")

    with pytest.raises(EvidenceProviderResponseError) as error:
        fetch(YahooNewsEvidenceProvider(broken_feed, search_loader=search), "AAPL", company_names=["Apple Inc."])
    message = str(error.value).casefold()
    assert "channels down" in message
    for marker in ("unsupported", "invalid", "malformed", "unusable"):
        assert marker not in message          # frontier maps it to NEWS_PROVIDER_UNAVAILABLE


def test_failed_feed_with_reachable_search_and_no_news_is_no_data():
    search = Search({"XYZ": {"news": [], "quotes": []}})
    result = fetch(YahooNewsEvidenceProvider(failing_feed, search_loader=search), "XYZ")
    assert result.status is EvidenceFetchStatus.NO_DATA
    assert any("TICKER_FEED FAILED" in warning for warning in result.warnings)


def test_injected_feed_alone_keeps_single_channel_behaviour():
    provider = YahooNewsEvidenceProvider(lambda t: [])
    result = fetch(provider, "AAPL")
    assert result.status is EvidenceFetchStatus.NO_DATA


def test_frontier_aliases_include_normalized_names():
    aliases = aliases_from_eligibility({"all_decisions": [
        {"exchange": "XETRA", "symbol": "DRW3", "name": "Drägerwerk AG & Co. KGaA (pref)"},
        {"exchange": "BIT", "symbol": "UCG", "name": "UniCredit S.p.A."},
    ]})
    assert "Drägerwerk" in aliases["XETRA:DRW3"]
    assert "UniCredit" in aliases["BIT:UCG"]


def test_european_listing_falls_back_to_base_symbol():
    # Live 2026-10-05: "Bayerische Motoren Werke" returned no news; "BMW" does.
    search = Search({
        "BMW.DE": {"news": [], "quotes": [{"symbol": "BMW.DE", "shortname": "BAYERISCHE MOTOREN WERKE AG   S"}]},
        "Bayerische Motoren Werke": {"news": [], "quotes": []},
        "BMW": {"news": [flat("BMW backs new 3 Series with EUR2bn German investment"),
                         flat("Mercedes trims outlook")], "quotes": []},
    })
    result = fetch(YahooNewsEvidenceProvider(failing_feed, search_loader=search), "BMW.DE")
    assert [item.metadata["headline"] for item in result.items] == [
        "BMW backs new 3 Series with EUR2bn German investment"
    ]
    assert result.items[0].evidence.metadata["news_channel"] == "SEARCH_BASE_SYMBOL"
    assert search.queries == ["BMW.DE", "Bayerische Motoren Werke", "BMW"]


def test_yahoo_upper_case_short_name_is_searched_in_title_case():
    search = Search({
        "UCG.MI": {"news": [], "quotes": [{"symbol": "UCG.MI", "shortname": "UNICREDIT"}]},
        "Unicredit": {"news": [flat("UniCredit raises stake in Commerzbank")], "quotes": []},
    })
    result = fetch(YahooNewsEvidenceProvider(failing_feed, search_loader=search), "UCG.MI")
    assert len(result.items) == 1
    assert search.queries == ["UCG.MI", "Unicredit"]


def test_relevant_items_outside_the_window_report_channels():
    old = flat("Banco BPM board meets", hours=24 * 45)
    search = Search({"BAMI.MI": {"news": [], "quotes": []}, "Banco Bpm": {"news": [old], "quotes": []}})
    result = fetch(YahooNewsEvidenceProvider(failing_feed, search_loader=search), "BAMI.MI",
                   company_names=["Banco Bpm"])
    assert result.status is EvidenceFetchStatus.NO_DATA
    assert result.metadata["news_channels"]["SEARCH_NAME"] == "OK:1"
