from datetime import datetime, timezone

from app.ai.evidence_provider import (
    EvidenceFetchStatus,
    EvidenceKind,
    EvidenceRequest,
)
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider

NOW = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)


def flat_news():
    return [
        {
            "title": "UiPath announces product update",
            "publisher": "Example Wire",
            "link": "https://example.invalid/a",
            "providerPublishTime": 1788112800,
            "summary": "The company described a new automation capability.",
        }
    ]


def nested_news():
    return [
        {
            "content": {
                "title": "UiPath schedules investor event",
                "provider": {"displayName": "Example News"},
                "canonicalUrl": {"url": "https://example.invalid/b"},
                "pubDate": "2026-08-29T12:00:00Z",
                "summary": "Management will discuss strategy.",
            }
        }
    ]


def test_provider_name():
    assert YahooNewsEvidenceProvider(lambda t: []).provider_name == "YAHOO_NEWS"


def test_flat_news_is_normalized():
    result = YahooNewsEvidenceProvider(lambda t: flat_news()).fetch(
        EvidenceRequest(ticker="path")
    )
    assert result.status == EvidenceFetchStatus.SUCCESS
    assert result.ticker == "PATH"
    assert len(result.items) == 1
    item = result.items[0]
    assert item.kind == EvidenceKind.NEWS
    assert "Headline:" in item.evidence.text
    assert item.source.source_name == "Example Wire"


def test_nested_yfinance_shape_is_supported():
    result = YahooNewsEvidenceProvider(lambda t: nested_news()).fetch(
        EvidenceRequest(ticker="PATH")
    )
    item = result.items[0]
    assert "investor event" in item.evidence.text
    assert item.source.source_url == "https://example.invalid/b"
    assert item.source.source_name == "Example News"


def test_source_and_evidence_provenance_are_linked():
    item = YahooNewsEvidenceProvider(lambda t: nested_news()).fetch(
        EvidenceRequest(ticker="PATH")
    ).items[0]
    assert item.evidence.metadata["source_id"] == item.source.source_id
    assert item.evidence.metadata["url"] == item.source.source_url
    assert item.source.provider == "YAHOO_NEWS"


def test_empty_feed_returns_no_data_without_fabrication():
    result = YahooNewsEvidenceProvider(lambda t: []).fetch(
        EvidenceRequest(ticker="PATH")
    )
    assert result.status == EvidenceFetchStatus.NO_DATA
    assert result.items == []


def test_unusable_items_return_no_data():
    result = YahooNewsEvidenceProvider(lambda t: [{"publisher": "X"}]).fetch(
        EvidenceRequest(ticker="PATH")
    )
    assert result.status == EvidenceFetchStatus.NO_DATA
    assert result.items == []


def test_duplicates_are_removed():
    raw = flat_news() + flat_news()
    result = YahooNewsEvidenceProvider(lambda t: raw).fetch(
        EvidenceRequest(ticker="PATH")
    )
    assert len(result.items) == 1


def test_max_items_is_respected():
    raw = flat_news()
    second = dict(raw[0])
    second["title"] = "Second headline"
    second["link"] = "https://example.invalid/c"
    result = YahooNewsEvidenceProvider(lambda t: [raw[0], second]).fetch(
        EvidenceRequest(ticker="PATH", max_items=1)
    )
    assert len(result.items) == 1


def test_future_items_are_filtered_when_as_of_is_given():
    raw = [{
        "title": "Future item",
        "publisher": "Example",
        "link": "https://example.invalid/future",
        "pubDate": "2026-09-05T12:00:00Z",
    }]
    result = YahooNewsEvidenceProvider(lambda t: raw).fetch(
        EvidenceRequest(ticker="PATH", as_of=NOW)
    )
    assert result.status == EvidenceFetchStatus.NO_DATA


def test_old_items_are_filtered_by_lookback():
    raw = [{
        "title": "Old item",
        "publisher": "Example",
        "link": "https://example.invalid/old",
        "pubDate": "2026-06-01T12:00:00Z",
    }]
    result = YahooNewsEvidenceProvider(
        lambda t: raw, default_lookback_days=30
    ).fetch(EvidenceRequest(ticker="PATH", as_of=NOW))
    assert result.status == EvidenceFetchStatus.NO_DATA


def test_missing_publication_time_uses_retrieval_time():
    raw = [{
        "title": "No timestamp",
        "publisher": "Example",
        "link": "https://example.invalid/no-ts",
    }]
    result = YahooNewsEvidenceProvider(lambda t: raw).fetch(
        EvidenceRequest(ticker="PATH")
    )
    assert result.items[0].evidence.published_at is not None


def test_evidence_id_is_stable_for_same_item():
    provider = YahooNewsEvidenceProvider(lambda t: nested_news())
    a = provider.fetch(EvidenceRequest(ticker="PATH")).items[0].evidence.evidence_id
    b = provider.fetch(EvidenceRequest(ticker="PATH")).items[0].evidence.evidence_id
    assert a == b


def test_feed_order_does_not_change_selected_evidence_ids():
    raw = flat_news()
    second = dict(raw[0])
    second["title"] = "Second headline"
    second["link"] = "https://example.invalid/c"
    second["providerPublishTime"] = raw[0]["providerPublishTime"] - 60
    provider_a = YahooNewsEvidenceProvider(lambda t: [raw[0], second])
    provider_b = YahooNewsEvidenceProvider(lambda t: [second, raw[0]])
    req = EvidenceRequest(ticker="PATH", max_items=2, as_of=NOW)
    a = provider_a.fetch(req)
    b = provider_b.fetch(req)
    assert [x.evidence.evidence_id for x in a.items] == [
        x.evidence.evidence_id for x in b.items
    ]


def test_max_items_is_applied_after_canonical_recency_sort():
    old = dict(flat_news()[0])
    old["title"] = "Older"
    old["link"] = "https://example.invalid/old2"
    old["providerPublishTime"] = 1788110000
    new = dict(flat_news()[0])
    new["title"] = "Newer"
    new["link"] = "https://example.invalid/new2"
    new["providerPublishTime"] = 1788113000
    result = YahooNewsEvidenceProvider(lambda t: [old, new]).fetch(
        EvidenceRequest(ticker="PATH", max_items=1)
    )
    assert result.items[0].metadata["headline"] == "Newer"


def test_selection_policy_provenance_is_persisted():
    result = YahooNewsEvidenceProvider(lambda t: flat_news()).fetch(
        EvidenceRequest(ticker="PATH")
    )
    assert result.metadata["selection_policy_version"] == (
        "yahoo-news-selection-v3-multichannel"
    )
    assert result.metadata["selected_evidence_ids"] == [
        x.evidence.evidence_id for x in result.items
    ]
