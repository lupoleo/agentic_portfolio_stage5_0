from app.scanner.exchange_policy import (
    EnabledExchange,
    EnabledExchangePolicy,
    ExchangeCoverageScope,
)
from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.provider_composition import (
    UniverseCompositionStatus,
    compose_exchange_universe,
)
from app.scanner.universe_models import (
    ListingKey,
    RawMarketListing,
)


def policy_entry(
    exchange,
    provider_id,
    *,
    coverage=ExchangeCoverageScope.FULL_EXCHANGE,
):
    return EnabledExchange(
        exchange_code=exchange,
        provider_id=provider_id,
        coverage_scope=coverage,
    )


def policy(*entries):
    return EnabledExchangePolicy(
        policy_id="test-policy",
        policy_version="1",
        entries=entries,
    )


def listing(
    symbol,
    exchange,
    *,
    currency="EUR",
    source="TEST",
):
    return RawMarketListing(
        symbol=symbol,
        exchange=exchange,
        market=exchange,
        region="EUROPE",
        currency=currency,
        instrument_type="COMMON STOCK",
        name=symbol,
        country="TEST",
        source=source,
    )


def provider_result(
    *,
    exchange,
    provider_id,
    status=ExchangeProviderStatus.SUCCESS,
    listings=(),
    diagnostics=(),
    metadata=None,
):
    return ExchangeSymbolResult(
        provider_id=provider_id,
        provider_version="1",
        source_name=provider_id,
        source_url=f"https://example.test/{exchange}",
        request=ExchangeSymbolRequest(exchange),
        status=status,
        listings=listings,
        diagnostics=diagnostics,
        metadata=metadata or {},
    )


class FakeProvider(ExchangeSymbolProvider):
    def __init__(
        self,
        provider_id,
        result=None,
        *,
        error=None,
    ):
        self._provider_id = provider_id
        self.result = result
        self.error = error
        self.calls = []

    @property
    def provider_id(self):
        return self._provider_id

    @property
    def provider_version(self):
        return "1"

    def fetch(self, request):
        self.calls.append(request)

        if self.error is not None:
            raise self.error

        return self.result


def failed_result(exchange, provider_id):
    return provider_result(
        exchange=exchange,
        provider_id=provider_id,
        status=ExchangeProviderStatus.FAILED,
        diagnostics=(
            ExchangeProviderDiagnostic(
                code="SOURCE_FAILED",
                message="source unavailable",
            ),
        ),
    )


def test_success_composes_and_orders_multiple_exchanges():
    lu_id = "eodhd-exchange-symbols"
    bit_id = "borsa-italiana-ftse-mib"

    lu = FakeProvider(
        lu_id,
        provider_result(
            exchange="LU",
            provider_id=lu_id,
            listings=(listing("LXMPR", "LU"),),
        ),
    )
    bit = FakeProvider(
        bit_id,
        provider_result(
            exchange="BIT",
            provider_id=bit_id,
            listings=(listing("A2A", "BIT"),),
            metadata={
                "coverage_scope": "INDEX_FALLBACK",
                "full_exchange_coverage": False,
            },
        ),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", lu_id),
            policy_entry(
                "BIT",
                bit_id,
                coverage=(
                    ExchangeCoverageScope.INDEX_FALLBACK
                ),
            ),
        ),
        providers={
            "LU": lu,
            "BIT": bit,
        },
    )

    assert result.status is UniverseCompositionStatus.SUCCESS
    assert [
        (item.exchange, item.symbol)
        for item in result.universe
    ] == [
        ("BIT", "A2A"),
        ("LU", "LXMPR"),
    ]
    assert result.metadata["canonical_listing_count"] == 2
    assert result.metadata["index_fallback_count"] == 1


def test_partial_provider_makes_composition_partial():
    provider_id = "eodhd-exchange-symbols"
    diagnostic = ExchangeProviderDiagnostic(
        code="ROW_SKIPPED",
        message="bad row",
    )
    fake = FakeProvider(
        provider_id,
        provider_result(
            exchange="LU",
            provider_id=provider_id,
            status=ExchangeProviderStatus.PARTIAL,
            listings=(listing("LXMPR", "LU"),),
            diagnostics=(diagnostic,),
        ),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
        ),
        providers={"LU": fake},
    )

    assert result.status is UniverseCompositionStatus.PARTIAL
    assert len(result.universe) == 1
    assert result.diagnostics[0].code == "ROW_SKIPPED"


def test_failed_provider_with_valid_provider_is_partial():
    provider_id = "eodhd-exchange-symbols"

    valid = FakeProvider(
        provider_id,
        provider_result(
            exchange="LU",
            provider_id=provider_id,
            listings=(listing("LXMPR", "LU"),),
        ),
    )
    failed = FakeProvider(
        provider_id,
        failed_result("LSE", provider_id),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
            policy_entry("LSE", provider_id),
        ),
        providers={
            "LU": valid,
            "LSE": failed,
        },
    )

    assert result.status is UniverseCompositionStatus.PARTIAL
    assert len(result.universe) == 1
    assert result.metadata["failed_provider_count"] == 1


def test_all_failed_providers_fail_composition():
    provider_id = "eodhd-exchange-symbols"

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
        ),
        providers={
            "LU": FakeProvider(
                provider_id,
                failed_result("LU", provider_id),
            )
        },
    )

    assert result.status is UniverseCompositionStatus.FAILED
    assert result.universe == ()
    assert any(
        item.code == "NO_VALID_LISTINGS"
        for item in result.diagnostics
    )


def test_missing_provider_is_explicit():
    provider_id = "eodhd-exchange-symbols"

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
        ),
        providers={},
    )

    assert result.status is UniverseCompositionStatus.FAILED
    assert result.diagnostics[0].code == "MISSING_PROVIDER"


def test_provider_identity_mismatch_skips_io():
    expected_id = "eodhd-exchange-symbols"
    fake = FakeProvider("wrong-provider")

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", expected_id),
        ),
        providers={"LU": fake},
    )

    assert result.status is UniverseCompositionStatus.FAILED
    assert result.diagnostics[0].code == "PROVIDER_ID_MISMATCH"
    assert fake.calls == []


def test_unexpected_provider_exception_is_contained():
    provider_id = "eodhd-exchange-symbols"
    fake = FakeProvider(
        provider_id,
        error=RuntimeError("secret internal detail"),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
        ),
        providers={"LU": fake},
    )

    assert result.status is UniverseCompositionStatus.FAILED
    assert result.diagnostics[0].code == "PROVIDER_EXCEPTION"
    assert "secret internal detail" not in (
        result.diagnostics[0].message
    )


def test_wrong_exchange_listing_is_rejected():
    provider_id = "eodhd-exchange-symbols"
    fake = FakeProvider(
        provider_id,
        provider_result(
            exchange="LU",
            provider_id=provider_id,
            listings=(listing("ABC", "LSE"),),
        ),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
        ),
        providers={"LU": fake},
    )

    assert result.status is UniverseCompositionStatus.FAILED
    assert (
        result.diagnostics[0].code
        == "LISTING_EXCHANGE_MISMATCH"
    )


def test_index_fallback_requires_coverage_metadata():
    provider_id = "borsa-italiana-ftse-mib"
    fake = FakeProvider(
        provider_id,
        provider_result(
            exchange="BIT",
            provider_id=provider_id,
            listings=(listing("A2A", "BIT"),),
        ),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry(
                "BIT",
                provider_id,
                coverage=(
                    ExchangeCoverageScope.INDEX_FALLBACK
                ),
            ),
        ),
        providers={"BIT": fake},
    )

    assert result.status is UniverseCompositionStatus.FAILED
    assert (
        result.diagnostics[0].code
        == "COVERAGE_METADATA_MISMATCH"
    )


def test_canonical_conflict_fails_closed():
    provider_id = "eodhd-exchange-symbols"
    fake = FakeProvider(
        provider_id,
        provider_result(
            exchange="LU",
            provider_id=provider_id,
            listings=(
                listing("ABC", "LU", currency="EUR"),
                listing("ABC", "LU", currency="USD"),
            ),
        ),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
        ),
        providers={"LU": fake},
    )

    assert result.status is UniverseCompositionStatus.FAILED
    assert result.universe == ()
    assert (
        result.diagnostics[-1].code
        == "CANONICALIZATION_FAILED"
    )


def test_exclusions_are_applied_to_canonical_universe():
    provider_id = "eodhd-exchange-symbols"
    fake = FakeProvider(
        provider_id,
        provider_result(
            exchange="LU",
            provider_id=provider_id,
            listings=(
                listing("ABC", "LU"),
                listing("XYZ", "LU"),
            ),
        ),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
        ),
        providers={"LU": fake},
        exclusions=(ListingKey("LU", "ABC"),),
    )

    assert result.status is UniverseCompositionStatus.SUCCESS
    assert [
        item.symbol
        for item in result.universe
    ] == ["XYZ"]


def test_excluding_every_listing_fails_explicitly():
    provider_id = "eodhd-exchange-symbols"
    fake = FakeProvider(
        provider_id,
        provider_result(
            exchange="LU",
            provider_id=provider_id,
            listings=(listing("ABC", "LU"),),
        ),
    )

    result = compose_exchange_universe(
        policy=policy(
            policy_entry("LU", provider_id),
        ),
        providers={"LU": fake},
        exclusions=(ListingKey("LU", "ABC"),),
    )

    assert result.status is UniverseCompositionStatus.FAILED
    assert (
        result.diagnostics[-1].code
        == "EMPTY_CANONICAL_UNIVERSE"
    )
