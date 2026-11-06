from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.provider_cache import (
    CacheLookup,
    CacheLookupStatus,
    ExchangeSymbolFileCache,
)


class CacheRefreshMode(str, Enum):
    PREFER_CACHE = "PREFER_CACHE"
    FORCE_REFRESH = "FORCE_REFRESH"
    CACHE_ONLY = "CACHE_ONLY"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CachedExchangeSymbolProvider(ExchangeSymbolProvider):
    """
    Cache decorator for one ExchangeSymbolProvider.

    Fresh cached results preserve the original provider identity and
    fetched_at timestamp. Cache provenance is added to result metadata.
    """

    def __init__(
        self,
        provider: ExchangeSymbolProvider,
        cache: ExchangeSymbolFileCache,
        *,
        ttl: timedelta = timedelta(hours=24),
        refresh_mode: CacheRefreshMode = (
            CacheRefreshMode.PREFER_CACHE
        ),
        allow_stale_on_error: bool = False,
        max_stale_age: timedelta = timedelta(days=7),
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(provider, ExchangeSymbolProvider):
            raise TypeError(
                "provider must implement ExchangeSymbolProvider"
            )
        if not isinstance(cache, ExchangeSymbolFileCache):
            raise TypeError(
                "cache must be ExchangeSymbolFileCache"
            )
        if ttl <= timedelta(0):
            raise ValueError("ttl must be > 0")
        if max_stale_age <= timedelta(0):
            raise ValueError(
                "max_stale_age must be > 0"
            )

        self._provider = provider
        self._cache = cache
        self._ttl = ttl
        self._refresh_mode = CacheRefreshMode(refresh_mode)
        self._allow_stale_on_error = bool(
            allow_stale_on_error
        )
        self._max_stale_age = max_stale_age
        self._now = now

    @property
    def provider_id(self) -> str:
        return self._provider.provider_id

    @property
    def provider_version(self) -> str:
        return self._provider.provider_version

    @property
    def refresh_mode(self) -> CacheRefreshMode:
        return self._refresh_mode

    def fetch(
        self,
        request: ExchangeSymbolRequest,
    ) -> ExchangeSymbolResult:
        lookup: CacheLookup | None = None

        if (
            self._refresh_mode
            is not CacheRefreshMode.FORCE_REFRESH
        ):
            lookup = self._load(request)

            if lookup.status is CacheLookupStatus.FRESH:
                assert lookup.result is not None
                return self._with_cache_metadata(
                    lookup.result,
                    disposition="CACHE_HIT",
                    lookup=lookup,
                )

            if (
                self._refresh_mode
                is CacheRefreshMode.CACHE_ONLY
            ):
                return self._cache_only_failure(
                    request,
                    lookup,
                )

        live_result = self._fetch_live(request)

        if live_result.status is ExchangeProviderStatus.FAILED:
            stale_lookup = lookup
            if stale_lookup is None:
                stale_lookup = self._load(request)

            stale_result = self._stale_fallback(
                live_result=live_result,
                lookup=stale_lookup,
            )
            if stale_result is not None:
                return stale_result

            return self._with_cache_metadata(
                live_result,
                disposition="LIVE_FAILED",
                lookup=stale_lookup,
            )

        try:
            self._cache.store(
                live_result,
                ttl=self._ttl,
            )
        except Exception as exc:
            diagnostic = ExchangeProviderDiagnostic(
                code="CACHE_WRITE_FAILED",
                message=(
                    "Live provider result is valid but could not "
                    "be persisted to cache: "
                    f"{type(exc).__name__}"
                ),
            )
            return replace(
                live_result,
                status=ExchangeProviderStatus.PARTIAL,
                diagnostics=(
                    *live_result.diagnostics,
                    diagnostic,
                ),
                metadata=self._merged_metadata(
                    live_result,
                    disposition="LIVE_CACHE_WRITE_FAILED",
                    lookup=lookup,
                ),
            )

        return replace(
            live_result,
            metadata=self._merged_metadata(
                live_result,
                disposition="LIVE_REFRESH",
                lookup=lookup,
            ),
        )

    def _load(
        self,
        request: ExchangeSymbolRequest,
    ) -> CacheLookup:
        try:
            return self._cache.load(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                request=request,
            )
        except Exception as exc:
            return CacheLookup(
                status=CacheLookupStatus.INVALID,
                reason=type(exc).__name__,
            )

    def _fetch_live(
        self,
        request: ExchangeSymbolRequest,
    ) -> ExchangeSymbolResult:
        try:
            result = self._provider.fetch(request)
        except Exception as exc:
            return self._failed(
                request,
                code="PROVIDER_EXCEPTION",
                message=(
                    "Live provider raised unexpectedly: "
                    f"{type(exc).__name__}"
                ),
            )

        if not isinstance(result, ExchangeSymbolResult):
            return self._failed(
                request,
                code="INVALID_PROVIDER_RESULT",
                message=(
                    "Live provider did not return "
                    "ExchangeSymbolResult"
                ),
            )

        if (
            result.provider_id != self.provider_id
            or result.provider_version
            != self.provider_version
            or result.request.exchange_code
            != request.exchange_code
        ):
            return self._failed(
                request,
                code="INVALID_PROVIDER_RESULT",
                message=(
                    "Live provider result identity does not "
                    "match the cache request"
                ),
            )

        return result

    def _cache_only_failure(
        self,
        request: ExchangeSymbolRequest,
        lookup: CacheLookup,
    ) -> ExchangeSymbolResult:
        code_by_status = {
            CacheLookupStatus.MISS: "CACHE_MISS",
            CacheLookupStatus.STALE: "CACHE_STALE",
            CacheLookupStatus.INVALID: "CACHE_INVALID",
        }
        code = code_by_status.get(
            lookup.status,
            "CACHE_UNAVAILABLE",
        )

        return self._failed(
            request,
            code=code,
            message=(
                "CACHE_ONLY requires a valid fresh cache entry"
            ),
            metadata={
                "cache": self._cache_metadata(
                    disposition="CACHE_ONLY_FAILED",
                    lookup=lookup,
                )
            },
        )

    def _stale_fallback(
        self,
        *,
        live_result: ExchangeSymbolResult,
        lookup: CacheLookup,
    ) -> ExchangeSymbolResult | None:
        if not self._allow_stale_on_error:
            return None
        if lookup.status is not CacheLookupStatus.STALE:
            return None
        if lookup.result is None or lookup.expires_at is None:
            return None

        now = self._validated_now()
        stale_age = now - lookup.expires_at
        if stale_age < timedelta(0):
            stale_age = timedelta(0)
        if stale_age > self._max_stale_age:
            return None

        diagnostic = ExchangeProviderDiagnostic(
            code="STALE_CACHE_FALLBACK",
            message=(
                "Live provider failed; an explicitly allowed "
                "stale cache entry was used"
            ),
        )
        cached = lookup.result

        return replace(
            cached,
            status=ExchangeProviderStatus.PARTIAL,
            diagnostics=(
                *cached.diagnostics,
                diagnostic,
            ),
            metadata=self._merged_metadata(
                cached,
                disposition="STALE_FALLBACK",
                lookup=lookup,
                additional={
                    "stale_age_seconds": (
                        stale_age.total_seconds()
                    ),
                    "live_failure_codes": [
                        item.code
                        for item in live_result.diagnostics
                    ],
                },
            ),
        )

    def _with_cache_metadata(
        self,
        result: ExchangeSymbolResult,
        *,
        disposition: str,
        lookup: CacheLookup | None,
    ) -> ExchangeSymbolResult:
        return replace(
            result,
            metadata=self._merged_metadata(
                result,
                disposition=disposition,
                lookup=lookup,
            ),
        )

    def _merged_metadata(
        self,
        result: ExchangeSymbolResult,
        *,
        disposition: str,
        lookup: CacheLookup | None,
        additional: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(result.metadata)
        metadata["cache"] = self._cache_metadata(
            disposition=disposition,
            lookup=lookup,
            additional=additional,
        )
        return metadata

    @staticmethod
    def _cache_metadata(
        *,
        disposition: str,
        lookup: CacheLookup | None,
        additional: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "disposition": disposition,
        }

        if lookup is not None:
            value["lookup_status"] = lookup.status.value
            if lookup.cached_at is not None:
                value["cached_at"] = (
                    lookup.cached_at.isoformat()
                )
            if lookup.expires_at is not None:
                value["expires_at"] = (
                    lookup.expires_at.isoformat()
                )
            if lookup.reason is not None:
                value["invalid_reason"] = lookup.reason

        if additional:
            value.update(additional)

        return value

    def _validated_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _failed(
        self,
        request: ExchangeSymbolRequest,
        *,
        code: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> ExchangeSymbolResult:
        return ExchangeSymbolResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_name="Cached exchange symbol provider",
            source_url=None,
            request=request,
            status=ExchangeProviderStatus.FAILED,
            diagnostics=(
                ExchangeProviderDiagnostic(
                    code=code,
                    message=message,
                ),
            ),
            fetched_at=self._validated_now(),
            metadata={} if metadata is None else metadata,
        )
