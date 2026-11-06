from datetime import datetime, timezone

from app.scanner.exchange_provider import (
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
)
from app.scanner.providers.borsa_italiana_ftsemib import (
    FTSE_MIB_PAGE_1,
    FTSE_MIB_PAGE_2,
    BorsaItalianaDiagnostic,
    BorsaItalianaFTSEMIBResult,
    BorsaItalianaProviderStatus,
)
from app.scanner.providers.borsa_italiana_ftsemib_adapter import (
    BorsaItalianaFTSEMIBExchangeProvider,
)
from app.scanner.universe_models import RawMarketListing


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def listing():
    return RawMarketListing(
        symbol="A2A",
        exchange="BIT",
        market="EURONEXT_MILAN",
        region="EUROPE",
        currency="EUR",
        instrument_type="COMMON_STOCK",
        isin="IT0001233417",
        name="A2a",
        country="IT",
        source="Borsa Italiana FTSE MIB",
    )


def source_result(
    status,
    *,
    listings=(),
    diagnostics=(),
):
    return BorsaItalianaFTSEMIBResult(
        provider_id="borsa-italiana-ftse-mib",
        provider_version="1",
        source_name="Borsa Italiana FTSE MIB",
        source_urls=(
            FTSE_MIB_PAGE_1,
            FTSE_MIB_PAGE_2,
        ),
        status=status,
        listings=listings,
        diagnostics=diagnostics,
        fetched_at=NOW,
        metadata={
            "membership_reference_count": 40,
        },
    )


class FakeProvider:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def fetch(self):
        self.calls += 1
        return self.result


def test_success_is_adapted_to_common_contract():
    source = source_result(
        BorsaItalianaProviderStatus.SUCCESS,
        listings=(listing(),),
    )
    fake = FakeProvider(source)

    result = BorsaItalianaFTSEMIBExchangeProvider(
        provider=fake
    ).fetch(ExchangeSymbolRequest("bit"))

    assert result.status is ExchangeProviderStatus.SUCCESS
    assert result.listings == (listing(),)
    assert result.request.exchange_code == "BIT"
    assert result.fetched_at == NOW
    assert result.source_url == FTSE_MIB_PAGE_1
    assert fake.calls == 1


def test_partial_diagnostic_preserves_source_isin():
    diagnostic = BorsaItalianaDiagnostic(
        code="DETAIL_REJECTED",
        message="invalid detail",
        isin="IT0001233417",
    )
    source = source_result(
        BorsaItalianaProviderStatus.PARTIAL,
        listings=(listing(),),
        diagnostics=(diagnostic,),
    )

    result = BorsaItalianaFTSEMIBExchangeProvider(
        provider=FakeProvider(source)
    ).fetch(ExchangeSymbolRequest("BIT"))

    assert result.status is ExchangeProviderStatus.PARTIAL
    assert result.diagnostics[0].code == "DETAIL_REJECTED"
    assert "IT0001233417" in result.diagnostics[0].message
    assert result.metadata["source_diagnostics"] == (
        {
            "code": "DETAIL_REJECTED",
            "message": "invalid detail",
            "isin": "IT0001233417",
        },
    )


def test_failed_status_is_preserved():
    diagnostic = BorsaItalianaDiagnostic(
        code="MEMBERSHIP_FETCH_FAILED",
        message="HTTP 503",
    )
    source = source_result(
        BorsaItalianaProviderStatus.FAILED,
        diagnostics=(diagnostic,),
    )

    result = BorsaItalianaFTSEMIBExchangeProvider(
        provider=FakeProvider(source)
    ).fetch(ExchangeSymbolRequest("BIT"))

    assert result.status is ExchangeProviderStatus.FAILED
    assert result.listings == ()
    assert result.diagnostics[0].code == (
        "MEMBERSHIP_FETCH_FAILED"
    )


def test_unsupported_exchange_fails_without_io():
    source = source_result(
        BorsaItalianaProviderStatus.SUCCESS,
        listings=(listing(),),
    )
    fake = FakeProvider(source)

    result = BorsaItalianaFTSEMIBExchangeProvider(
        provider=fake
    ).fetch(ExchangeSymbolRequest("LSE"))

    assert result.status is ExchangeProviderStatus.FAILED
    assert result.diagnostics[0].code == "UNSUPPORTED_EXCHANGE"
    assert fake.calls == 0


def test_coverage_is_explicitly_index_fallback():
    source = source_result(
        BorsaItalianaProviderStatus.SUCCESS,
        listings=(listing(),),
    )

    result = BorsaItalianaFTSEMIBExchangeProvider(
        provider=FakeProvider(source)
    ).fetch(ExchangeSymbolRequest("BIT"))

    assert result.metadata["coverage_scope"] == "INDEX_FALLBACK"
    assert result.metadata["index_name"] == "FTSE_MIB"
    assert result.metadata["full_exchange_coverage"] is False
    assert result.metadata["source_urls"] == (
        FTSE_MIB_PAGE_1,
        FTSE_MIB_PAGE_2,
    )
