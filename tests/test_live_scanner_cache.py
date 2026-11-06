from types import SimpleNamespace

import pytest

import tools.live_scanner_cache as live
from app.scanner.eodhd_exchange_discovery import (
    ExchangeDescriptor,
    ExchangeDiscoveryStatus,
)
from app.scanner.exchange_policy import (
    ExchangeCoverageScope,
)


def descriptor(code):
    return ExchangeDescriptor(
        code=code,
        name=f"{code} Exchange",
        operating_mic=None,
        country="Test Country",
        currency="EUR",
        country_iso2=None,
        country_iso3=None,
        region="EUROPE",
        is_virtual=False,
    )


class FakeDiscoveryProvider:
    result = None

    def __init__(
        self,
        *,
        api_token,
        timeout_seconds,
    ):
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds

    def discover(self):
        return self.result


def test_exchange_argument_accepts_arbitrary_code():
    assert live.exchange_code_argument(" pa ") == "PA"
    assert live.exchange_code_argument("xetra") == "XETRA"


def test_exchange_argument_rejects_blank_code():
    with pytest.raises(
        Exception,
        match="must not be blank",
    ):
        live.exchange_code_argument("   ")


def test_live_policy_is_dynamic_for_requested_exchange():
    policy = live.live_policy("PA")

    assert policy.enabled_exchange_codes == ("BIT", "PA")

    by_exchange = {
        item.exchange_code: item
        for item in policy.enabled_entries
    }
    assert (
        by_exchange["PA"].provider_id
        == "eodhd-exchange-symbols"
    )
    assert (
        by_exchange["PA"].coverage_scope
        is ExchangeCoverageScope.FULL_EXCHANGE
    )
    assert (
        by_exchange["BIT"].coverage_scope
        is ExchangeCoverageScope.INDEX_FALLBACK
    )


def test_discovery_resolves_requested_descriptor(
    monkeypatch,
):
    expected = descriptor("XETRA")
    FakeDiscoveryProvider.result = SimpleNamespace(
        status=ExchangeDiscoveryStatus.SUCCESS,
        exchanges=(
            descriptor("LU"),
            expected,
        ),
    )
    monkeypatch.setattr(
        live,
        "EODHDExchangeDiscoveryProvider",
        FakeDiscoveryProvider,
    )

    actual, status = live.discover_exchange_descriptor(
        api_token="test-token",
        exchange_code="XETRA",
        timeout_seconds=12.0,
    )

    assert actual is expected
    assert status is ExchangeDiscoveryStatus.SUCCESS


def test_discovery_rejects_unknown_exchange(
    monkeypatch,
):
    FakeDiscoveryProvider.result = SimpleNamespace(
        status=ExchangeDiscoveryStatus.SUCCESS,
        exchanges=(descriptor("LU"),),
    )
    monkeypatch.setattr(
        live,
        "EODHDExchangeDiscoveryProvider",
        FakeDiscoveryProvider,
    )

    with pytest.raises(
        ValueError,
        match="not present",
    ):
        live.discover_exchange_descriptor(
            api_token="test-token",
            exchange_code="UNKNOWN",
            timeout_seconds=12.0,
        )
