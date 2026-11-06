from datetime import datetime, timedelta, timezone

import pytest

from app.scanner.cached_exchange_provider import (
    CachedExchangeSymbolProvider,
    CacheRefreshMode,
)
from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.provider_cache import (
    ExchangeSymbolFileCache,
)
from app.scanner.universe_models import RawMarketListing


NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
REQUEST = ExchangeSymbolRequest("LU")


def listing(symbol="ABC"):
    return RawMarketListing(
        symbol=symbol,
        exchange="LU",
        market="LUXEMBOURG STOCK EXCHANGE",
        region="EUROPE",
        currency="EUR",
        instrument_type="COMMON STOCK",
        isin="LU0000000001",
        name="Example SA",
        country="LUXEMBOURG",
        source="TEST",
    )


def successful_result(
    request=REQUEST,
    *,
    symbol="ABC",
    status=ExchangeProviderStatus.SUCCESS,
    diagnostics=(),
    provider_id="test-provider",
    provider_version="1",
):
    return ExchangeSymbolResult(
        provider_id=provider_id,
        provider_version=provider_version,
        source_name="Test live source",
        source_url="https://example.test/symbols",
        request=request,
        status=status,
        listings=(listing(symbol),),
        diagnostics=tuple(diagnostics),
        fetched_at=NOW,
        metadata={"live": True},
    )


def failed_result(request=REQUEST):
    return ExchangeSymbolResult(
        provider_id="test-provider",
        provider_version="1",
        source_name="Test live source",
        source_url="https://example.test/symbols",
        request=request,
        status=ExchangeProviderStatus.FAILED,
        diagnostics=(
            ExchangeProviderDiagnostic(
                code="LIVE_FAILED",
                message="Live source unavailable",
            ),
        ),
        fetched_at=NOW,
    )


class FakeProvider(ExchangeSymbolProvider):
    def __init__(
        self,
        effect,
        *,
        provider_id="test-provider",
        provider_version="1",
    ):
        self.effect = effect
        self.calls = []
        self._provider_id = provider_id
        self._provider_version = provider_version

    @property
    def provider_id(self):
        return self._provider_id

    @property
    def provider_version(self):
        return self._provider_version

    def fetch(self, request):
        self.calls.append(request)
        if isinstance(self.effect, BaseException):
            raise self.effect
        if callable(self.effect):
            return self.effect(request, len(self.calls))
        return self.effect


class FailingWriteCache(ExchangeSymbolFileCache):
    def store(self, result, *, ttl):
        raise OSError("private local path must not leak")


def make_cache(tmp_path, clock):
    return ExchangeSymbolFileCache(
        tmp_path,
        now=lambda: clock[0],
    )


def wrapper(
    provider,
    cache,
    clock,
    **kwargs,
):
    return CachedExchangeSymbolProvider(
        provider,
        cache,
        now=lambda: clock[0],
        **kwargs,
    )


def seed(cache, value, ttl=timedelta(hours=24)):
    cache.store(value, ttl=ttl)


def test_prefer_cache_miss_fetches_live_and_persists(tmp_path):
    clock = [NOW]
    provider = FakeProvider(successful_result())
    cached = wrapper(
        provider,
        make_cache(tmp_path, clock),
        clock,
    )

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.SUCCESS
    assert len(provider.calls) == 1
    assert (
        value.metadata["cache"]["disposition"]
        == "LIVE_REFRESH"
    )


def test_second_fetch_uses_fresh_cache_without_live_call(
    tmp_path,
):
    clock = [NOW]
    provider = FakeProvider(successful_result())
    cached = wrapper(
        provider,
        make_cache(tmp_path, clock),
        clock,
    )

    first = cached.fetch(REQUEST)
    second = cached.fetch(REQUEST)

    assert len(provider.calls) == 1
    assert first.listings == second.listings
    assert second.fetched_at == NOW
    assert (
        second.metadata["cache"]["disposition"]
        == "CACHE_HIT"
    )
    assert (
        second.metadata["cache"]["lookup_status"]
        == "FRESH"
    )


def test_force_refresh_calls_live_even_with_fresh_cache(
    tmp_path,
):
    clock = [NOW]
    cache = make_cache(tmp_path, clock)
    seed(cache, successful_result(symbol="OLD"))

    provider = FakeProvider(
        successful_result(symbol="NEW")
    )
    cached = wrapper(
        provider,
        cache,
        clock,
        refresh_mode=CacheRefreshMode.FORCE_REFRESH,
    )

    value = cached.fetch(REQUEST)

    assert len(provider.calls) == 1
    assert value.listings[0].symbol == "NEW"
    assert (
        value.metadata["cache"]["disposition"]
        == "LIVE_REFRESH"
    )


def test_cache_only_fresh_hit_never_calls_live(tmp_path):
    clock = [NOW]
    cache = make_cache(tmp_path, clock)
    seed(cache, successful_result())

    provider = FakeProvider(
        AssertionError("live must not be called")
    )
    cached = wrapper(
        provider,
        cache,
        clock,
        refresh_mode=CacheRefreshMode.CACHE_ONLY,
    )

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.SUCCESS
    assert provider.calls == []
    assert (
        value.metadata["cache"]["disposition"]
        == "CACHE_HIT"
    )


def test_cache_only_miss_fails_without_live_call(tmp_path):
    clock = [NOW]
    provider = FakeProvider(successful_result())
    cached = wrapper(
        provider,
        make_cache(tmp_path, clock),
        clock,
        refresh_mode=CacheRefreshMode.CACHE_ONLY,
    )

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "CACHE_MISS"
    assert provider.calls == []


def test_cache_only_rejects_stale_entry(tmp_path):
    clock = [NOW]
    cache = make_cache(tmp_path, clock)
    seed(
        cache,
        successful_result(),
        ttl=timedelta(hours=1),
    )
    clock[0] = NOW + timedelta(hours=2)

    provider = FakeProvider(successful_result())
    cached = wrapper(
        provider,
        cache,
        clock,
        refresh_mode=CacheRefreshMode.CACHE_ONLY,
    )

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "CACHE_STALE"
    assert provider.calls == []


def test_prefer_cache_refreshes_stale_entry(tmp_path):
    clock = [NOW]
    cache = make_cache(tmp_path, clock)
    seed(
        cache,
        successful_result(symbol="OLD"),
        ttl=timedelta(hours=1),
    )
    clock[0] = NOW + timedelta(hours=2)

    provider = FakeProvider(
        successful_result(symbol="NEW")
    )
    cached = wrapper(provider, cache, clock)

    value = cached.fetch(REQUEST)

    assert len(provider.calls) == 1
    assert value.listings[0].symbol == "NEW"
    assert (
        value.metadata["cache"]["lookup_status"]
        == "STALE"
    )


def test_live_failure_does_not_use_stale_by_default(
    tmp_path,
):
    clock = [NOW]
    cache = make_cache(tmp_path, clock)
    seed(
        cache,
        successful_result(),
        ttl=timedelta(hours=1),
    )
    clock[0] = NOW + timedelta(hours=2)

    provider = FakeProvider(failed_result())
    cached = wrapper(provider, cache, clock)

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "LIVE_FAILED"
    assert (
        value.metadata["cache"]["disposition"]
        == "LIVE_FAILED"
    )


def test_explicit_stale_fallback_is_partial_and_audited(
    tmp_path,
):
    clock = [NOW]
    cache = make_cache(tmp_path, clock)
    seed(
        cache,
        successful_result(symbol="CACHED"),
        ttl=timedelta(hours=1),
    )
    clock[0] = NOW + timedelta(hours=2)

    provider = FakeProvider(failed_result())
    cached = wrapper(
        provider,
        cache,
        clock,
        allow_stale_on_error=True,
        max_stale_age=timedelta(days=1),
    )

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.PARTIAL
    assert value.listings[0].symbol == "CACHED"
    assert (
        value.diagnostics[-1].code
        == "STALE_CACHE_FALLBACK"
    )
    assert (
        value.metadata["cache"]["disposition"]
        == "STALE_FALLBACK"
    )
    assert (
        value.metadata["cache"]["stale_age_seconds"]
        == 3600.0
    )
    assert value.metadata["cache"]["live_failure_codes"] == [
        "LIVE_FAILED"
    ]


def test_stale_entry_older_than_limit_is_not_used(tmp_path):
    clock = [NOW]
    cache = make_cache(tmp_path, clock)
    seed(
        cache,
        successful_result(),
        ttl=timedelta(hours=1),
    )
    clock[0] = NOW + timedelta(days=8)

    provider = FakeProvider(failed_result())
    cached = wrapper(
        provider,
        cache,
        clock,
        allow_stale_on_error=True,
        max_stale_age=timedelta(days=7),
    )

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.FAILED
    assert value.diagnostics[0].code == "LIVE_FAILED"


def test_provider_exception_is_contained_without_message_leak(
    tmp_path,
):
    clock = [NOW]
    provider = FakeProvider(
        RuntimeError("secret transport details")
    )
    cached = wrapper(
        provider,
        make_cache(tmp_path, clock),
        clock,
    )

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.FAILED
    assert (
        value.diagnostics[0].code
        == "PROVIDER_EXCEPTION"
    )
    assert "RuntimeError" in value.diagnostics[0].message
    assert (
        "secret transport details"
        not in value.diagnostics[0].message
    )


def test_invalid_live_result_identity_fails_closed(tmp_path):
    clock = [NOW]
    provider = FakeProvider(
        successful_result(provider_id="wrong-provider")
    )
    cached = wrapper(
        provider,
        make_cache(tmp_path, clock),
        clock,
    )

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.FAILED
    assert (
        value.diagnostics[0].code
        == "INVALID_PROVIDER_RESULT"
    )


def test_cache_write_failure_preserves_live_listings_as_partial(
    tmp_path,
):
    clock = [NOW]
    provider = FakeProvider(successful_result())
    cache = FailingWriteCache(
        tmp_path,
        now=lambda: clock[0],
    )
    cached = wrapper(provider, cache, clock)

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.PARTIAL
    assert value.listings[0].symbol == "ABC"
    assert (
        value.diagnostics[-1].code
        == "CACHE_WRITE_FAILED"
    )
    assert (
        "private local path"
        not in value.diagnostics[-1].message
    )


def test_invalid_cache_is_ignored_and_replaced_by_live(
    tmp_path,
):
    clock = [NOW]
    cache = make_cache(tmp_path, clock)
    path = cache.path_for(
        provider_id="test-provider",
        provider_version="1",
        exchange_code="LU",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    provider = FakeProvider(successful_result())
    cached = wrapper(provider, cache, clock)

    value = cached.fetch(REQUEST)

    assert value.status is ExchangeProviderStatus.SUCCESS
    assert len(provider.calls) == 1
    assert (
        value.metadata["cache"]["lookup_status"]
        == "INVALID"
    )


def test_partial_live_result_is_cached_and_remains_partial(
    tmp_path,
):
    clock = [NOW]
    partial = successful_result(
        status=ExchangeProviderStatus.PARTIAL,
        diagnostics=(
            ExchangeProviderDiagnostic(
                code="ROW_SKIPPED",
                message="Malformed row",
            ),
        ),
    )
    provider = FakeProvider(partial)
    cached = wrapper(
        provider,
        make_cache(tmp_path, clock),
        clock,
    )

    first = cached.fetch(REQUEST)
    second = cached.fetch(REQUEST)

    assert first.status is ExchangeProviderStatus.PARTIAL
    assert second.status is ExchangeProviderStatus.PARTIAL
    assert second.diagnostics[0].code == "ROW_SKIPPED"
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "ttl",
    [
        timedelta(0),
        timedelta(seconds=-1),
    ],
)
def test_ttl_must_be_positive(tmp_path, ttl):
    clock = [NOW]

    with pytest.raises(ValueError, match="ttl"):
        wrapper(
            FakeProvider(successful_result()),
            make_cache(tmp_path, clock),
            clock,
            ttl=ttl,
        )


def test_max_stale_age_must_be_positive(tmp_path):
    clock = [NOW]

    with pytest.raises(ValueError, match="max_stale_age"):
        wrapper(
            FakeProvider(successful_result()),
            make_cache(tmp_path, clock),
            clock,
            max_stale_age=timedelta(0),
        )
