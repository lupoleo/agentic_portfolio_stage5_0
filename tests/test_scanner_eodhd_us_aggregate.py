import json
from urllib.error import HTTPError, URLError

import pytest

from app.scanner.exchange_provider import (
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
)
from app.scanner.providers.eodhd_us_aggregate import (
    EODHDUSAggregateProvider,
    parse_us_aggregate_row,
)


def row(
    code="NVDA",
    *,
    exchange="NASDAQ",
    currency="USD",
    instrument_type="Common Stock",
    isin="US67066G1040",
    name="NVIDIA Corporation",
    country="USA",
):
    return {
        "Code": code,
        "Name": name,
        "Country": country,
        "Exchange": exchange,
        "Currency": currency,
        "Type": instrument_type,
        "Isin": isin,
    }


class FakeResponse:
    def __init__(self, payload, *, raw=False):
        self.payload = payload if raw else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def opener_for(payload, captured=None, *, raw=False):
    def opener(url, timeout):
        if captured is not None:
            captured["url"] = url
            captured["timeout"] = timeout
        return FakeResponse(payload, raw=raw)

    return opener


def provider(payload, **kwargs):
    return EODHDUSAggregateProvider(
        api_token="secret-token",
        opener=opener_for(payload),
        **kwargs,
    )


def test_token_is_required(monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="EODHD_API_TOKEN"):
        EODHDUSAggregateProvider()


def test_timeout_must_be_positive():
    with pytest.raises(ValueError, match="> 0"):
        EODHDUSAggregateProvider(api_token="secret", timeout_seconds=0)


def test_parser_preserves_provider_native_venue():
    value = parse_us_aggregate_row(row())
    assert value.symbol == "NVDA"
    assert value.exchange == "NASDAQ"
    assert value.market == "NASDAQ"
    assert value.region == "US"
    assert value.currency == "USD"
    assert value.instrument_type == "COMMON STOCK"
    assert value.isin == "US67066G1040"
    assert value.country == "USA"


def test_null_isin_is_supported():
    assert parse_us_aggregate_row(row(isin=None)).isin is None


def test_only_us_request_scope_is_supported_without_fetch():
    called = False

    def opener(url, timeout):
        nonlocal called
        called = True
        return FakeResponse([])

    value = EODHDUSAggregateProvider(
        api_token="secret",
        opener=opener,
    ).fetch(ExchangeSymbolRequest("NASDAQ"))

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "UNSUPPORTED_REQUEST_SCOPE"
    assert value.source_url.endswith("/US")
    assert called is False


def test_success_is_sorted_and_auditable():
    captured = {}
    value = EODHDUSAggregateProvider(
        api_token="secret-token",
        timeout_seconds=7,
        opener=opener_for(
            [
                row("IBM", exchange="NYSE", isin="US4592001014"),
                row("AAPL", exchange="NASDAQ", isin="US0378331005"),
                row("MSFT", exchange="NASDAQ", isin="US5949181045"),
            ],
            captured,
        ),
    ).fetch(ExchangeSymbolRequest("us"))

    assert value.status is ExchangeProviderStatus.SUCCESS
    assert [(item.exchange, item.symbol) for item in value.listings] == [
        ("NASDAQ", "AAPL"),
        ("NASDAQ", "MSFT"),
        ("NYSE", "IBM"),
    ]
    assert value.source_url.endswith("/US")
    assert "api_token" not in value.source_url
    assert "secret-token" not in value.source_url
    assert "api_token=secret-token" in captured["url"]
    assert captured["timeout"] == 7
    assert value.metadata["request_scope"] == "US"
    assert value.metadata["venue_count"] == 2
    assert value.metadata["venue_listing_counts"] == {
        "NASDAQ": 2,
        "NYSE": 1,
    }


def test_disabled_and_residual_venues_are_preserved_without_diagnostic():
    value = provider(
        [
            row("NEWETF", exchange="US", instrument_type="ETF"),
            row("OTC1", exchange="PINK"),
            row("LEGACY", exchange="NYSE MKT"),
            row("FUND1", exchange="NMFQS", instrument_type="Fund"),
        ]
    ).fetch(ExchangeSymbolRequest("US"))

    assert value.status is ExchangeProviderStatus.SUCCESS
    assert {item.exchange for item in value.listings} == {
        "NMFQS",
        "NYSE MKT",
        "PINK",
        "US",
    }
    assert value.diagnostics == ()


def test_same_symbol_on_different_venues_is_preserved():
    value = provider(
        [
            row("ABC", exchange="NASDAQ", currency="USD"),
            row("ABC", exchange="NYSE", currency="USD"),
        ]
    ).fetch(ExchangeSymbolRequest("US"))

    assert value.status is ExchangeProviderStatus.SUCCESS
    assert [(item.exchange, item.symbol) for item in value.listings] == [
        ("NASDAQ", "ABC"),
        ("NYSE", "ABC"),
    ]


def test_malformed_row_produces_partial():
    value = provider([row(), {"Code": "BROKEN"}]).fetch(
        ExchangeSymbolRequest("US")
    )
    assert value.status is ExchangeProviderStatus.PARTIAL
    assert len(value.listings) == 1
    assert value.diagnostics[0].code == "ROW_SKIPPED"
    assert value.metadata["rejected_row_count"] == 1


def test_non_object_row_produces_partial():
    value = provider([row(), "broken"]).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.PARTIAL
    assert value.diagnostics[0].code == "ROW_SKIPPED"


def test_identical_duplicate_is_deduplicated_by_exchange_and_symbol():
    value = provider([row(), row()]).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.SUCCESS
    assert len(value.listings) == 1
    assert value.metadata["duplicate_row_count"] == 1


def test_conflicting_duplicate_listing_fails_closed():
    value = provider([row(), row(currency="EUR")]).fetch(
        ExchangeSymbolRequest("US")
    )
    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "CONFLICTING_DUPLICATE_LISTING"
    assert "NASDAQ:NVDA" in value.diagnostics[0].message


def test_no_valid_listings_fails_closed():
    value = provider([{"Code": "BROKEN"}]).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "NO_VALID_LISTINGS"


def test_non_array_payload_fails_closed():
    value = provider({"error": "bad"}).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "INVALID_PAYLOAD"


def test_invalid_json_fails_closed():
    value = EODHDUSAggregateProvider(
        api_token="secret",
        opener=opener_for(b"not-json", raw=True),
    ).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "INVALID_JSON"


def test_timeout_has_explicit_diagnostic():
    def opener(url, timeout):
        raise TimeoutError

    value = EODHDUSAggregateProvider(
        api_token="secret",
        opener=opener,
    ).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "TIMEOUT"


def test_urlerror_timeout_has_explicit_diagnostic():
    def opener(url, timeout):
        raise URLError(TimeoutError())

    value = EODHDUSAggregateProvider(
        api_token="secret",
        opener=opener,
    ).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "TIMEOUT"


def test_network_error_does_not_expose_reason_text():
    def opener(url, timeout):
        raise URLError("secret-looking-host-message")

    value = EODHDUSAggregateProvider(
        api_token="secret",
        opener=opener,
    ).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "NETWORK_ERROR"
    assert "secret-looking-host-message" not in value.diagnostics[0].message


def test_http_error_has_explicit_diagnostic():
    def opener(url, timeout):
        raise HTTPError(url, 429, "rate limited", None, None)

    value = EODHDUSAggregateProvider(
        api_token="secret",
        opener=opener,
    ).fetch(ExchangeSymbolRequest("US"))
    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "HTTP_ERROR"
    assert "429" in value.diagnostics[0].message


def test_instrument_type_metadata_is_aggregated():
    value = provider(
        [
            row("NVDA", instrument_type="Common Stock"),
            row("QQQ", instrument_type="ETF"),
            row("SPY", exchange="NYSE ARCA", instrument_type="ETF"),
        ]
    ).fetch(ExchangeSymbolRequest("US"))
    assert value.metadata["instrument_type_counts"] == {
        "COMMON STOCK": 1,
        "ETF": 2,
    }
