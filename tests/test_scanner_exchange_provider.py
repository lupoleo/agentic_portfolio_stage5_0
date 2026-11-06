from datetime import datetime, timezone

import pytest

from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.universe_models import RawMarketListing


NOW = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)


def listing(exchange="US"):
    return RawMarketListing(
        symbol="NVDA",
        exchange=exchange,
        market="US",
        region="US",
        currency="USD",
        instrument_type="COMMON STOCK",
        source="TEST",
    )


def diagnostic():
    return ExchangeProviderDiagnostic(
        code="ROW_SKIPPED",
        message="Malformed row",
        symbol="nvda",
    )


def result(status, listings=(), diagnostics=(), fetched_at=NOW):
    return ExchangeSymbolResult(
        provider_id="test-exchange-provider",
        provider_version="1",
        source_name="Test exchange symbol source",
        source_url="https://example.test",
        request=ExchangeSymbolRequest("us"),
        status=status,
        listings=listings,
        diagnostics=diagnostics,
        fetched_at=fetched_at,
    )


def test_request_canonicalizes_exchange_code():
    assert ExchangeSymbolRequest(" us ").exchange_code == "US"


def test_success_is_auditable():
    value = result(ExchangeProviderStatus.SUCCESS, (listing(),))
    assert value.provider_id == "test-exchange-provider"
    assert value.request.exchange_code == "US"


def test_success_requires_listing():
    with pytest.raises(ValueError, match="SUCCESS"):
        result(ExchangeProviderStatus.SUCCESS)


def test_partial_requires_diagnostics():
    with pytest.raises(ValueError, match="diagnostics"):
        result(ExchangeProviderStatus.PARTIAL, (listing(),))


def test_valid_partial():
    value = result(
        ExchangeProviderStatus.PARTIAL,
        (listing(),),
        (diagnostic(),),
    )
    assert value.status is ExchangeProviderStatus.PARTIAL


def test_failed_requires_diagnostic():
    with pytest.raises(ValueError, match="diagnostics"):
        result(ExchangeProviderStatus.FAILED)


def test_failed_cannot_contain_listing():
    with pytest.raises(ValueError, match="must not contain"):
        result(
            ExchangeProviderStatus.FAILED,
            (listing(),),
            (diagnostic(),),
        )


def test_valid_failed_result():
    value = result(
        ExchangeProviderStatus.FAILED,
        (),
        (diagnostic(),),
    )
    assert value.listings == ()
    assert value.diagnostics[0].code == "ROW_SKIPPED"


def test_timestamp_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        result(
            ExchangeProviderStatus.SUCCESS,
            (listing(),),
            (),
            datetime(2026, 9, 14, 10, 0),
        )


def test_diagnostic_is_canonicalized():
    value = diagnostic()
    assert value.code == "ROW_SKIPPED"
    assert value.symbol == "NVDA"
