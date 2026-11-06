import json

import pytest

from app.scanner.eodhd_exchange_discovery import (
    EODHD_PUBLIC_EXCHANGES_SOURCE_URL,
    EODHDExchangeDiscoveryProvider,
    ExchangeDiscoveryStatus,
    classify_exchange_region,
    parse_exchange_descriptor,
)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def opener_for(payload, captured):
    def opener(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse(payload)

    return opener


def sample_rows():
    return [
        {
            "Name": "USA Stocks",
            "Code": "US",
            "OperatingMIC": "XNAS, XNYS, OTCM, XCBO",
            "Country": "USA",
            "Currency": "USD",
            "CountryISO2": "US",
            "CountryISO3": "USA",
        },
        {
            "Name": "London Exchange",
            "Code": "LSE",
            "OperatingMIC": "XLON",
            "Country": "UK",
            "Currency": "GBP",
            "CountryISO2": "GB",
            "CountryISO3": "GBR",
        },
        {
            "Name": "Cryptocurrencies",
            "Code": "CC",
            "OperatingMIC": None,
            "Country": "Unknown",
            "Currency": "Unknown",
            "CountryISO2": "",
            "CountryISO3": "",
        },
    ]


def test_token_is_required(monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="EODHD_API_TOKEN"):
        EODHDExchangeDiscoveryProvider()


def test_token_can_come_from_environment(monkeypatch):
    monkeypatch.setenv("EODHD_API_TOKEN", "secret")
    provider = EODHDExchangeDiscoveryProvider(
        opener=opener_for(sample_rows(), {})
    )
    assert provider.provider_id == "eodhd-exchange-discovery"


def test_discovery_parses_documented_exchange_contract():
    captured = {}
    provider = EODHDExchangeDiscoveryProvider(
        api_token="secret-token",
        timeout_seconds=7,
        opener=opener_for(sample_rows(), captured),
    )

    result = provider.discover()

    assert result.status is ExchangeDiscoveryStatus.SUCCESS
    assert [item.code for item in result.exchanges] == ["CC", "LSE", "US"]
    by_code = {item.code: item for item in result.exchanges}

    assert by_code["US"].region == "US"
    assert by_code["US"].operating_mic == "XNAS, XNYS, OTCM, XCBO"
    assert by_code["LSE"].region == "EUROPE"
    assert by_code["LSE"].country_iso2 == "GB"
    assert by_code["CC"].is_virtual is True
    assert by_code["CC"].region is None

    assert captured["timeout"] == 7
    assert "api_token=secret-token" in captured["url"]


def test_result_never_persists_token_in_source_url():
    result = EODHDExchangeDiscoveryProvider(
        api_token="top-secret",
        opener=opener_for(sample_rows(), {}),
    ).discover()

    assert result.source_url == EODHD_PUBLIC_EXCHANGES_SOURCE_URL
    assert "top-secret" not in result.source_url
    assert "api_token" not in result.source_url.lower()


def test_metadata_counts_regions_and_virtual_entries():
    result = EODHDExchangeDiscoveryProvider(
        api_token="secret",
        opener=opener_for(sample_rows(), {}),
    ).discover()

    assert result.metadata == {
        "exchange_count": 3,
        "physical_exchange_count": 2,
        "virtual_exchange_count": 1,
        "us_exchange_count": 1,
        "europe_exchange_count": 1,
        "other_exchange_count": 0,
        "unclassified_exchange_count": 1,
    }


def test_malformed_row_produces_partial_not_global_failure():
    rows = sample_rows() + [{"Name": "Broken", "Code": ""}]
    result = EODHDExchangeDiscoveryProvider(
        api_token="secret",
        opener=opener_for(rows, {}),
    ).discover()

    assert result.status is ExchangeDiscoveryStatus.PARTIAL
    assert len(result.exchanges) == 3
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "ROW_SKIPPED"


def test_non_array_payload_fails_closed():
    result = EODHDExchangeDiscoveryProvider(
        api_token="secret",
        opener=opener_for({"error": "bad"}, {}),
    ).discover()

    assert result.status is ExchangeDiscoveryStatus.FAILED
    assert result.exchanges == ()
    assert result.diagnostics[0].code == "INVALID_PAYLOAD"


def test_duplicate_exchange_code_fails_closed():
    rows = sample_rows() + [sample_rows()[0]]
    result = EODHDExchangeDiscoveryProvider(
        api_token="secret",
        opener=opener_for(rows, {}),
    ).discover()

    assert result.status is ExchangeDiscoveryStatus.FAILED
    assert result.diagnostics[0].code == "DUPLICATE_EXCHANGE_CODE"


def test_region_classification_is_listing_market_not_issuer_logic():
    assert classify_exchange_region(code="US", country_iso2="US") == "US"
    assert classify_exchange_region(code="LSE", country_iso2="GB") == "EUROPE"
    assert classify_exchange_region(code="XETRA", country_iso2="DE") == "EUROPE"
    assert classify_exchange_region(code="AU", country_iso2="AU") == "OTHER"
    assert classify_exchange_region(code="CC", country_iso2=None) is None


def test_parser_canonicalizes_documented_fields():
    value = parse_exchange_descriptor(
        {
            "Name": " london exchange ",
            "Code": " lse ",
            "OperatingMIC": " xlon ",
            "Country": " UK ",
            "Currency": " gbp ",
            "CountryISO2": " gb ",
            "CountryISO3": " gbr ",
        }
    )
    assert value.code == "LSE"
    assert value.operating_mic == "XLON"
    assert value.currency == "GBP"
    assert value.country_iso2 == "GB"
    assert value.region == "EUROPE"
