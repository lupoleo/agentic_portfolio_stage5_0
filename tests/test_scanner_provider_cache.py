import json
from datetime import datetime, timedelta, timezone

import pytest

from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.provider_cache import (
    CacheLookupStatus,
    ExchangeSymbolFileCache,
)
from app.scanner.universe_models import RawMarketListing


NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


def listing(
    *,
    symbol="ABC",
    exchange="LU",
):
    return RawMarketListing(
        symbol=symbol,
        exchange=exchange,
        market="LUXEMBOURG STOCK EXCHANGE",
        region="EUROPE",
        currency="EUR",
        instrument_type="COMMON STOCK",
        isin="LU0000000001",
        name="Example SA",
        country="LUXEMBOURG",
        source="TEST",
    )


def result(
    *,
    provider_id="test-provider",
    provider_version="1",
    exchange="LU",
    status=ExchangeProviderStatus.SUCCESS,
    listings=None,
    diagnostics=(),
    metadata=None,
    source_url="https://example.test/symbols",
):
    if listings is None:
        listings = (listing(exchange=exchange),)

    return ExchangeSymbolResult(
        provider_id=provider_id,
        provider_version=provider_version,
        source_name="Test source",
        source_url=source_url,
        request=ExchangeSymbolRequest(exchange),
        status=status,
        listings=tuple(listings),
        diagnostics=tuple(diagnostics),
        fetched_at=NOW,
        metadata={} if metadata is None else metadata,
    )


def cache(tmp_path, clock=None):
    if clock is None:
        clock = [NOW]
    return (
        ExchangeSymbolFileCache(
            tmp_path,
            now=lambda: clock[0],
        ),
        clock,
    )


def test_fresh_round_trip_preserves_result(tmp_path):
    store, _ = cache(tmp_path)
    original = result(
        metadata={
            "coverage_scope": "FULL_EXCHANGE",
            "counts": [1, 2],
        }
    )

    path = store.store(
        original,
        ttl=timedelta(hours=24),
    )
    lookup = store.load(
        provider_id=original.provider_id,
        provider_version=original.provider_version,
        request=original.request,
    )

    assert path.is_file()
    assert lookup.status is CacheLookupStatus.FRESH
    assert lookup.result == original
    assert lookup.cached_at == NOW
    assert lookup.expires_at == NOW + timedelta(hours=24)


def test_partial_result_round_trip(tmp_path):
    store, _ = cache(tmp_path)
    original = result(
        status=ExchangeProviderStatus.PARTIAL,
        diagnostics=(
            ExchangeProviderDiagnostic(
                code="ROW_SKIPPED",
                message="Malformed row",
                symbol="BAD",
            ),
        ),
    )

    store.store(original, ttl=timedelta(hours=1))
    lookup = store.load(
        provider_id=original.provider_id,
        provider_version=original.provider_version,
        request=original.request,
    )

    assert lookup.status is CacheLookupStatus.FRESH
    assert (
        lookup.result.status
        is ExchangeProviderStatus.PARTIAL
    )
    assert lookup.result.diagnostics[0].code == "ROW_SKIPPED"


def test_missing_entry_is_miss(tmp_path):
    store, _ = cache(tmp_path)

    lookup = store.load(
        provider_id="missing",
        provider_version="1",
        request=ExchangeSymbolRequest("LU"),
    )

    assert lookup.status is CacheLookupStatus.MISS
    assert lookup.result is None


def test_entry_becomes_stale_after_ttl(tmp_path):
    store, clock = cache(tmp_path)
    original = result()

    store.store(original, ttl=timedelta(hours=24))
    clock[0] = NOW + timedelta(hours=24)

    lookup = store.load(
        provider_id=original.provider_id,
        provider_version=original.provider_version,
        request=original.request,
    )

    assert lookup.status is CacheLookupStatus.STALE
    assert lookup.result == original


def test_provider_version_has_separate_cache_identity(tmp_path):
    store, _ = cache(tmp_path)
    original = result(provider_version="1")

    store.store(original, ttl=timedelta(hours=24))
    lookup = store.load(
        provider_id=original.provider_id,
        provider_version="2",
        request=original.request,
    )

    assert lookup.status is CacheLookupStatus.MISS


def test_exchange_has_separate_cache_identity(tmp_path):
    store, _ = cache(tmp_path)
    original = result(exchange="LU")

    store.store(original, ttl=timedelta(hours=24))
    lookup = store.load(
        provider_id=original.provider_id,
        provider_version=original.provider_version,
        request=ExchangeSymbolRequest("BIT"),
    )

    assert lookup.status is CacheLookupStatus.MISS


def test_corrupt_json_is_invalid(tmp_path):
    store, _ = cache(tmp_path)
    path = store.path_for(
        provider_id="test-provider",
        provider_version="1",
        exchange_code="LU",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    lookup = store.load(
        provider_id="test-provider",
        provider_version="1",
        request=ExchangeSymbolRequest("LU"),
    )

    assert lookup.status is CacheLookupStatus.INVALID
    assert lookup.result is None
    assert lookup.reason == "JSONDecodeError"


def test_modified_payload_fails_integrity_check(tmp_path):
    store, _ = cache(tmp_path)
    original = result()
    path = store.store(
        original,
        ttl=timedelta(hours=24),
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["result"]["listings"][0][
        "symbol"
    ] = "TAMPERED"
    path.write_text(
        json.dumps(document),
        encoding="utf-8",
    )

    lookup = store.load(
        provider_id=original.provider_id,
        provider_version=original.provider_version,
        request=original.request,
    )

    assert lookup.status is CacheLookupStatus.INVALID
    assert lookup.reason == "ValueError"


def test_failed_result_is_not_cached(tmp_path):
    store, _ = cache(tmp_path)
    failed = result(
        status=ExchangeProviderStatus.FAILED,
        listings=(),
        diagnostics=(
            ExchangeProviderDiagnostic(
                code="FETCH_FAILED",
                message="Unavailable",
            ),
        ),
    )

    with pytest.raises(ValueError, match="FAILED"):
        store.store(failed, ttl=timedelta(hours=1))


@pytest.mark.parametrize(
    "ttl",
    [
        timedelta(0),
        timedelta(seconds=-1),
    ],
)
def test_ttl_must_be_positive(tmp_path, ttl):
    store, _ = cache(tmp_path)

    with pytest.raises(ValueError, match="ttl"):
        store.store(result(), ttl=ttl)


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_token": "do-not-store"},
        {"nested": {"authorization": "Bearer secret"}},
        {"value": {"not", "json"}},
    ],
)
def test_secret_or_non_json_metadata_is_rejected(
    tmp_path,
    metadata,
):
    store, _ = cache(tmp_path)

    with pytest.raises(ValueError):
        store.store(
            result(metadata=metadata),
            ttl=timedelta(hours=1),
        )


def test_authenticated_source_url_is_rejected(tmp_path):
    store, _ = cache(tmp_path)

    with pytest.raises(ValueError, match="secret-like"):
        store.store(
            result(
                source_url=(
                    "https://example.test/symbols?"
                    "api_token=secret"
                )
            ),
            ttl=timedelta(hours=1),
        )


def test_atomic_write_leaves_no_temporary_files(tmp_path):
    store, _ = cache(tmp_path)

    final_path = store.store(
        result(),
        ttl=timedelta(hours=1),
    )

    assert final_path.is_file()
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


def test_naive_clock_is_rejected(tmp_path):
    store = ExchangeSymbolFileCache(
        tmp_path,
        now=lambda: datetime(2026, 9, 17, 8, 0),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        store.store(
            result(),
            ttl=timedelta(hours=1),
        )


def test_cache_filename_is_deterministic_and_safe(tmp_path):
    store, _ = cache(tmp_path)

    path = store.path_for(
        provider_id=" Provider/Test ",
        provider_version=" V 1 ",
        exchange_code=" lu ",
    )

    assert path.name == "provider-test__v-1__lu.json"
