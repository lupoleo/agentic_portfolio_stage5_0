from datetime import datetime, timedelta, timezone

from app.scanner.cached_exchange_provider import (
    CachedExchangeSymbolProvider,
)
from app.scanner.exchange_policy import (
    EnabledExchange,
    EnabledExchangePolicy,
    ExchangeCoverageScope,
    scanner_v1_pilot_policy,
)
from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolResult,
)
from app.scanner.provider_cache import (
    ExchangeSymbolFileCache,
)
from app.scanner.provider_composition import (
    UniverseCompositionStatus,
    compose_exchange_universe,
)
from app.scanner.universe_models import RawMarketListing


NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


class FakeProvider(ExchangeSymbolProvider):
    def __init__(
        self,
        provider_id,
        result_factory,
    ):
        self._provider_id = provider_id
        self.result_factory = result_factory
        self.calls = 0

    @property
    def provider_id(self):
        return self._provider_id

    @property
    def provider_version(self):
        return "1"

    def fetch(self, request):
        self.calls += 1
        return self.result_factory(request)


def live_result(
    provider,
    request,
    *,
    symbol,
    coverage_scope,
    status=ExchangeProviderStatus.SUCCESS,
    diagnostics=(),
):
    metadata = {
        "coverage_scope": coverage_scope.value,
        "full_exchange_coverage": (
            coverage_scope
            is ExchangeCoverageScope.FULL_EXCHANGE
        ),
    }
    if coverage_scope is ExchangeCoverageScope.INDEX_FALLBACK:
        metadata["index_name"] = "FTSE_MIB"

    return ExchangeSymbolResult(
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        source_name=f"Test {request.exchange_code}",
        source_url=(
            f"https://example.test/"
            f"{request.exchange_code.lower()}"
        ),
        request=request,
        status=status,
        listings=(
            RawMarketListing(
                symbol=symbol,
                exchange=request.exchange_code,
                market=(
                    "EURONEXT_MILAN"
                    if request.exchange_code == "BIT"
                    else "LUXEMBOURG_STOCK_EXCHANGE"
                ),
                region="EUROPE",
                currency="EUR",
                instrument_type="COMMON_STOCK",
                isin=(
                    "IT0000000001"
                    if request.exchange_code == "BIT"
                    else "LU0000000001"
                ),
                name=f"{symbol} Company",
                country=(
                    "IT"
                    if request.exchange_code == "BIT"
                    else "LU"
                ),
                source="TEST",
            ),
        ),
        diagnostics=tuple(diagnostics),
        fetched_at=NOW,
        metadata=metadata,
    )


def failed_result(provider, request):
    return ExchangeSymbolResult(
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        source_name="Test failed source",
        source_url=None,
        request=request,
        status=ExchangeProviderStatus.FAILED,
        diagnostics=(
            ExchangeProviderDiagnostic(
                code="LIVE_FAILED",
                message="Live provider unavailable",
            ),
        ),
        fetched_at=NOW,
    )


def cached(
    provider,
    cache,
    clock,
    **kwargs,
):
    return CachedExchangeSymbolProvider(
        provider,
        cache,
        ttl=kwargs.pop(
            "ttl",
            timedelta(hours=24),
        ),
        now=lambda: clock[0],
        **kwargs,
    )


def test_second_composition_run_is_entirely_from_cache(
    tmp_path,
):
    clock = [NOW]
    cache = ExchangeSymbolFileCache(
        tmp_path,
        now=lambda: clock[0],
    )

    lu = FakeProvider("eodhd-exchange-symbols", None)
    bit = FakeProvider("borsa-italiana-ftse-mib", None)

    lu.result_factory = lambda request: live_result(
        lu,
        request,
        symbol="LXMPR",
        coverage_scope=ExchangeCoverageScope.FULL_EXCHANGE,
    )
    bit.result_factory = lambda request: live_result(
        bit,
        request,
        symbol="A2A",
        coverage_scope=ExchangeCoverageScope.INDEX_FALLBACK,
    )

    providers = {
        "LU": cached(lu, cache, clock),
        "BIT": cached(bit, cache, clock),
    }

    first = compose_exchange_universe(
        policy=scanner_v1_pilot_policy(),
        providers=providers,
    )
    second = compose_exchange_universe(
        policy=scanner_v1_pilot_policy(),
        providers=providers,
    )

    assert first.status is UniverseCompositionStatus.SUCCESS
    assert second.status is UniverseCompositionStatus.SUCCESS
    assert len(first.universe) == 2
    assert len(second.universe) == 2
    assert lu.calls == 1
    assert bit.calls == 1
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert {
        item.metadata["cache"]["disposition"]
        for item in second.provider_results
    } == {"CACHE_HIT"}
    assert second.diagnostics == ()


def test_stale_fallback_makes_composition_partial(
    tmp_path,
):
    clock = [NOW]
    cache = ExchangeSymbolFileCache(
        tmp_path,
        now=lambda: clock[0],
    )

    policy = EnabledExchangePolicy(
        policy_id="stale-test",
        policy_version="1",
        entries=(
            EnabledExchange(
                exchange_code="LU",
                provider_id="eodhd-exchange-symbols",
                coverage_scope=(
                    ExchangeCoverageScope.FULL_EXCHANGE
                ),
            ),
        ),
    )

    lu = FakeProvider("eodhd-exchange-symbols", None)
    lu.result_factory = lambda request: live_result(
        lu,
        request,
        symbol="LXMPR",
        coverage_scope=ExchangeCoverageScope.FULL_EXCHANGE,
    )

    provider = cached(
        lu,
        cache,
        clock,
        ttl=timedelta(hours=1),
        allow_stale_on_error=True,
        max_stale_age=timedelta(days=1),
    )

    first = compose_exchange_universe(
        policy=policy,
        providers={"LU": provider},
    )
    assert first.status is UniverseCompositionStatus.SUCCESS

    clock[0] = NOW + timedelta(hours=2)
    lu.result_factory = lambda request: failed_result(
        lu,
        request,
    )

    second = compose_exchange_universe(
        policy=policy,
        providers={"LU": provider},
    )

    assert second.status is UniverseCompositionStatus.PARTIAL
    assert len(second.universe) == 1
    assert second.universe[0].symbol == "LXMPR"
    assert lu.calls == 2
    assert [
        item.code
        for item in second.diagnostics
    ] == ["STALE_CACHE_FALLBACK"]
    assert (
        second.provider_results[0]
        .metadata["cache"]["disposition"]
        == "STALE_FALLBACK"
    )
