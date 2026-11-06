from datetime import datetime, timezone

import pytest

from app.scanner.exchange_policy import (
    EnabledExchange,
    EnabledExchangePolicy,
    ExchangeCoverageScope,
    scanner_v1_production_policy,
)
from app.scanner.exchange_provider import (
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.provider_composition import (
    UniverseCompositionStatus,
    compose_exchange_universe,
)
from app.scanner.universe_models import RawMarketListing


NOW = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)


def listing(symbol: str, exchange: str) -> RawMarketListing:
    return RawMarketListing(
        symbol=symbol,
        exchange=exchange,
        market=exchange,
        region="US",
        currency="USD",
        instrument_type="COMMON STOCK",
        source="TEST",
    )


class RecordingProvider(ExchangeSymbolProvider):
    provider_id = "eodhd-us-aggregate-symbols"
    provider_version = "1"

    def __init__(self, listings):
        self.listings = tuple(listings)
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        return ExchangeSymbolResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_name="Test US aggregate",
            source_url=(
                "https://eodhd.com/api/exchange-symbol-list/US"
            ),
            request=request,
            status=ExchangeProviderStatus.SUCCESS,
            listings=self.listings,
            fetched_at=NOW,
            metadata={"request_scope": "US"},
        )


def aggregate_policy(*venues: str) -> EnabledExchangePolicy:
    return EnabledExchangePolicy(
        entries=tuple(
            EnabledExchange(
                exchange_code=venue,
                acquisition_code="US",
                provider_id="eodhd-us-aggregate-symbols",
                coverage_scope=(
                    ExchangeCoverageScope.AGGREGATE_VENUES
                ),
            )
            for venue in venues
        )
    )


def test_default_acquisition_code_preserves_old_contract():
    entry = EnabledExchange(
        exchange_code=" xetra ",
        provider_id="provider",
        coverage_scope=ExchangeCoverageScope.FULL_EXCHANGE,
    )
    assert entry.exchange_code == "XETRA"
    assert entry.acquisition_code == "XETRA"


def test_acquisition_code_is_canonicalized():
    entry = EnabledExchange(
        exchange_code="nasdaq",
        acquisition_code=" us ",
        provider_id="provider",
        coverage_scope=ExchangeCoverageScope.AGGREGATE_VENUES,
    )
    assert entry.exchange_code == "NASDAQ"
    assert entry.acquisition_code == "US"


def test_shared_acquisition_requires_one_contract():
    with pytest.raises(ValueError, match="conflicting acquisition"):
        EnabledExchangePolicy(
            entries=(
                EnabledExchange(
                    exchange_code="NASDAQ",
                    acquisition_code="US",
                    provider_id="one",
                    coverage_scope=(
                        ExchangeCoverageScope.AGGREGATE_VENUES
                    ),
                ),
                EnabledExchange(
                    exchange_code="NYSE",
                    acquisition_code="US",
                    provider_id="two",
                    coverage_scope=(
                        ExchangeCoverageScope.AGGREGATE_VENUES
                    ),
                ),
            )
        )


def test_production_policy_matches_frozen_s21h_decision():
    policy = scanner_v1_production_policy()
    assert policy.policy_id == "scanner-v1-production"
    assert len(policy.enabled_entries) == 24

    us_entries = tuple(
        entry
        for entry in policy.enabled_entries
        if entry.acquisition_code == "US"
    )
    assert {entry.exchange_code for entry in us_entries} == {
        "AMEX",
        "BATS",
        "NASDAQ",
        "NYSE",
        "NYSE ARCA",
    }
    assert {
        entry.coverage_scope for entry in us_entries
    } == {ExchangeCoverageScope.AGGREGATE_VENUES}

    bit = next(
        entry
        for entry in policy.enabled_entries
        if entry.exchange_code == "BIT"
    )
    assert bit.coverage_scope is ExchangeCoverageScope.INDEX_FALLBACK


def test_production_policy_has_twenty_acquisitions():
    policy = scanner_v1_production_policy()
    assert len(
        {entry.acquisition_code for entry in policy.enabled_entries}
    ) == 20


def test_aggregate_provider_executes_once_and_filters_downstream():
    provider = RecordingProvider(
        (
            listing("AAPL", "NASDAQ"),
            listing("IBM", "NYSE"),
            listing("OTC1", "PINK"),
            listing("FUND1", "NMFQS"),
        )
    )

    result = compose_exchange_universe(
        policy=aggregate_policy("NASDAQ", "NYSE"),
        providers={"US": provider},
    )

    assert result.status is UniverseCompositionStatus.SUCCESS
    assert [request.exchange_code for request in provider.requests] == [
        "US"
    ]
    assert {
        (item.exchange, item.symbol) for item in result.universe
    } == {("NASDAQ", "AAPL"), ("NYSE", "IBM")}
    assert result.metadata["enabled_exchange_count"] == 2
    assert result.metadata["acquisition_count"] == 1
    assert result.metadata["executed_provider_count"] == 1
    assert result.metadata["raw_listing_count"] == 4
    assert result.metadata["canonical_listing_count"] == 2
    assert result.metadata["aggregate_venue_count"] == 2


def test_aggregate_result_retains_provider_audit_record():
    provider = RecordingProvider((listing("AAPL", "NASDAQ"),))
    result = compose_exchange_universe(
        policy=aggregate_policy("NASDAQ"),
        providers={"US": provider},
    )
    assert len(result.provider_results) == 1
    assert result.provider_results[0].request.exchange_code == "US"


def test_registry_is_keyed_by_acquisition_not_output_venue():
    provider = RecordingProvider((listing("AAPL", "NASDAQ"),))
    result = compose_exchange_universe(
        policy=aggregate_policy("NASDAQ"),
        providers={"NASDAQ": provider},
    )
    assert result.status is UniverseCompositionStatus.FAILED
    assert any(
        diagnostic.code == "MISSING_PROVIDER"
        and diagnostic.exchange_code == "US"
        for diagnostic in result.diagnostics
    )


def test_nonaggregate_provider_still_rejects_wrong_exchange():
    provider = RecordingProvider((listing("IBM", "NYSE"),))
    provider.provider_id = "single-provider"
    policy = EnabledExchangePolicy(
        entries=(
            EnabledExchange(
                exchange_code="NASDAQ",
                provider_id="single-provider",
                coverage_scope=ExchangeCoverageScope.FULL_EXCHANGE,
            ),
        )
    )
    result = compose_exchange_universe(
        policy=policy,
        providers={"NASDAQ": provider},
    )
    assert result.status is UniverseCompositionStatus.FAILED
    assert any(
        item.code == "LISTING_EXCHANGE_MISMATCH"
        for item in result.diagnostics
    )
