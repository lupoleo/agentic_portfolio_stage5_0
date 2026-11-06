import json

import pytest

from app.scanner.eodhd_exchange_discovery import ExchangeDescriptor
from app.scanner.exchange_provider import (
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
)
from app.scanner.providers.eodhd_exchange_symbols import (
    EODHDExchangeSymbolProvider,
    parse_exchange_symbol_row,
)


def descriptor(
    code="LU",
    *,
    region="EUROPE",
    is_virtual=False,
):
    return ExchangeDescriptor(
        code=code,
        name="Luxembourg Stock Exchange",
        operating_mic="XLUX",
        country="Luxembourg",
        currency="EUR",
        country_iso2="LU",
        country_iso3="LUX",
        region=region,
        is_virtual=is_virtual,
    )


def row(
    code="ALCHA",
    *,
    currency="USD",
    isin="US0137411031",
    exchange="LU",
):
    return {
        "Code": code,
        "Name": "Alchip Technologies, Ltd.",
        "Country": "Luxembourg",
        "Exchange": exchange,
        "Currency": currency,
        "Type": "Common Stock",
        "Isin": isin,
    }


class FakeResponse:
    def __init__(self, payload, *, raw=False):
        self.payload = (
            payload
            if raw
            else json.dumps(payload).encode("utf-8")
        )

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
    return EODHDExchangeSymbolProvider(
        descriptor=descriptor(),
        api_token="secret-token",
        opener=opener_for(payload),
        **kwargs,
    )


def test_token_is_required(monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)

    with pytest.raises(ValueError, match="EODHD_API_TOKEN"):
        EODHDExchangeSymbolProvider(
            descriptor=descriptor()
        )


def test_virtual_descriptor_is_rejected():
    with pytest.raises(
        ValueError,
        match="physical classified",
    ):
        EODHDExchangeSymbolProvider(
            descriptor=descriptor(
                code="CC",
                region=None,
                is_virtual=True,
            ),
            api_token="secret",
        )


def test_parser_maps_live_contract_to_raw_listing():
    value = parse_exchange_symbol_row(
        row(),
        descriptor=descriptor(),
        expected_exchange_code="LU",
    )

    assert value.symbol == "ALCHA"
    assert value.exchange == "LU"
    assert value.market == "LUXEMBOURG STOCK EXCHANGE"
    assert value.region == "EUROPE"
    assert value.currency == "USD"
    assert value.instrument_type == "COMMON STOCK"
    assert value.isin == "US0137411031"
    assert value.name == "Alchip Technologies, Ltd."
    assert value.country == "LUXEMBOURG"


def test_null_isin_is_supported():
    value = parse_exchange_symbol_row(
        row(isin=None),
        descriptor=descriptor(),
        expected_exchange_code="LU",
    )

    assert value.isin is None


def test_success_is_sorted_and_auditable():
    captured = {}

    value = EODHDExchangeSymbolProvider(
        descriptor=descriptor(),
        api_token="secret-token",
        timeout_seconds=7,
        opener=opener_for(
            [
                row("SOFAF", isin=None),
                row("ALCHA"),
            ],
            captured,
        ),
    ).fetch(ExchangeSymbolRequest("lu"))

    assert value.status is ExchangeProviderStatus.SUCCESS
    assert [item.symbol for item in value.listings] == [
        "ALCHA",
        "SOFAF",
    ]
    assert value.source_url.endswith("/LU")
    assert "api_token" not in value.source_url
    assert "secret-token" not in value.source_url
    assert "api_token=secret-token" in captured["url"]
    assert captured["timeout"] == 7
    assert value.metadata["listing_count"] == 2


def test_malformed_row_produces_partial():
    value = provider(
        [
            row(),
            {"Code": "BROKEN"},
        ]
    ).fetch(ExchangeSymbolRequest("LU"))

    assert value.status is ExchangeProviderStatus.PARTIAL
    assert len(value.listings) == 1
    assert value.diagnostics[0].code == "ROW_SKIPPED"


def test_row_exchange_mismatch_produces_partial():
    value = provider(
        [
            row(),
            row("BAD", exchange="LSE"),
        ]
    ).fetch(ExchangeSymbolRequest("LU"))

    assert value.status is ExchangeProviderStatus.PARTIAL
    assert [item.symbol for item in value.listings] == ["ALCHA"]
    assert "exchange mismatch" in value.diagnostics[0].message


def test_request_descriptor_mismatch_fails_before_fetch():
    called = False

    def opener(url, timeout):
        nonlocal called
        called = True
        return FakeResponse([])

    value = EODHDExchangeSymbolProvider(
        descriptor=descriptor(),
        api_token="secret",
        opener=opener,
    ).fetch(ExchangeSymbolRequest("LSE"))

    assert value.status is ExchangeProviderStatus.FAILED
    assert (
        value.diagnostics[0].code
        == "EXCHANGE_DESCRIPTOR_MISMATCH"
    )
    assert called is False


def test_non_array_payload_fails_closed():
    value = provider(
        {"error": "bad"}
    ).fetch(ExchangeSymbolRequest("LU"))

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "INVALID_PAYLOAD"


def test_invalid_json_fails_closed():
    value = EODHDExchangeSymbolProvider(
        descriptor=descriptor(),
        api_token="secret",
        opener=opener_for(
            b"not-json",
            raw=True,
        ),
    ).fetch(ExchangeSymbolRequest("LU"))

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "INVALID_JSON"


def test_timeout_has_explicit_diagnostic():
    def opener(url, timeout):
        raise TimeoutError

    value = EODHDExchangeSymbolProvider(
        descriptor=descriptor(),
        api_token="secret",
        opener=opener,
    ).fetch(ExchangeSymbolRequest("LU"))

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "TIMEOUT"


def test_identical_duplicate_is_deduplicated():
    value = provider(
        [
            row(),
            row(),
        ]
    ).fetch(ExchangeSymbolRequest("LU"))

    assert value.status is ExchangeProviderStatus.SUCCESS
    assert len(value.listings) == 1
    assert value.metadata["duplicate_row_count"] == 1


def test_conflicting_duplicate_fails_closed():
    value = provider(
        [
            row(),
            row(currency="EUR"),
        ]
    ).fetch(ExchangeSymbolRequest("LU"))

    assert value.status is ExchangeProviderStatus.FAILED
    assert (
        value.diagnostics[0].code
        == "CONFLICTING_DUPLICATE_SYMBOL"
    )


def test_no_valid_listings_fails_closed():
    value = provider(
        [{"Code": "BROKEN"}]
    ).fetch(ExchangeSymbolRequest("LU"))

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "NO_VALID_LISTINGS"
