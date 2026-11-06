from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.universe_models import RawMarketListing


CACHE_SCHEMA_VERSION = 1

_SECRET_KEY_PARTS = (
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
)

_SECRET_TEXT_PATTERNS = (
    "api_token=",
    "authorization:",
    "bearer ",
)


class CacheLookupStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISS = "MISS"
    INVALID = "INVALID"


@dataclass(frozen=True)
class CacheLookup:
    status: CacheLookupStatus
    result: ExchangeSymbolResult | None = None
    cached_at: datetime | None = None
    expires_at: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status in {
            CacheLookupStatus.FRESH,
            CacheLookupStatus.STALE,
        }:
            if self.result is None:
                raise ValueError(
                    f"{self.status.value} requires result"
                )
            if self.cached_at is None or self.expires_at is None:
                raise ValueError(
                    f"{self.status.value} requires cache timestamps"
                )

        if self.status in {
            CacheLookupStatus.MISS,
            CacheLookupStatus.INVALID,
        } and self.result is not None:
            raise ValueError(
                f"{self.status.value} must not contain result"
            )

        for value in (self.cached_at, self.expires_at):
            if (
                value is not None
                and (
                    value.tzinfo is None
                    or value.utcoffset() is None
                )
            ):
                raise ValueError(
                    "cache timestamps must be timezone-aware"
                )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a timestamp string")

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")

    return parsed.astimezone(timezone.utc)


def _required_mapping(
    value: Any,
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonblank string")
    return value.strip()


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    normalized = value.strip()
    return normalized or None


def _json_safe(value: Any, path: str = "metadata") -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        if isinstance(value, str):
            lowered = value.casefold()
            if any(
                pattern in lowered
                for pattern in _SECRET_TEXT_PATTERNS
            ):
                raise ValueError(
                    f"{path} contains secret-like text"
                )
        return value

    if isinstance(value, float):
        if value != value or value in {
            float("inf"),
            float("-inf"),
        }:
            raise ValueError(
                f"{path} contains non-finite float"
            )
        return value

    if isinstance(value, (list, tuple)):
        return [
            _json_safe(item, f"{path}[]")
            for item in value
        ]

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(
                    f"{path} keys must be strings"
                )

            key = raw_key.strip()
            lowered_key = key.casefold()
            if any(
                part in lowered_key
                for part in _SECRET_KEY_PARTS
            ):
                raise ValueError(
                    f"{path}.{key} is secret-like"
                )

            normalized[key] = _json_safe(
                item,
                f"{path}.{key}",
            )
        return normalized

    raise ValueError(
        f"{path} contains unsupported type "
        f"{type(value).__name__}"
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _safe_component(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("cache key component must not be blank")

    safe = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    safe = safe.strip(".-")
    if not safe:
        raise ValueError("cache key component is not usable")
    return safe


class ExchangeSymbolFileCache:
    """
    Deterministic JSON cache for ExchangeSymbolResult.

    Cache identity is:
        schema version + provider id + provider version + exchange code.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._root = Path(root)
        self._now = now

    @property
    def root(self) -> Path:
        return self._root

    def path_for(
        self,
        *,
        provider_id: str,
        provider_version: str,
        exchange_code: str,
    ) -> Path:
        filename = (
            f"{_safe_component(provider_id)}__"
            f"{_safe_component(provider_version)}__"
            f"{_safe_component(exchange_code)}.json"
        )
        return self._root / filename

    def load(
        self,
        *,
        provider_id: str,
        provider_version: str,
        request: ExchangeSymbolRequest,
    ) -> CacheLookup:
        path = self.path_for(
            provider_id=provider_id,
            provider_version=provider_version,
            exchange_code=request.exchange_code,
        )

        if not path.is_file():
            return CacheLookup(CacheLookupStatus.MISS)

        try:
            document = json.loads(
                path.read_text(encoding="utf-8")
            )
            document = _required_mapping(
                document,
                "cache document",
            )

            payload = _required_mapping(
                document.get("payload"),
                "payload",
            )
            integrity = _required_mapping(
                document.get("integrity"),
                "integrity",
            )

            algorithm = _required_string(
                integrity.get("algorithm"),
                "integrity.algorithm",
            )
            expected_digest = _required_string(
                integrity.get("sha256"),
                "integrity.sha256",
            )

            if algorithm != "SHA-256":
                raise ValueError(
                    "unsupported integrity algorithm"
                )
            if not hmac.compare_digest(
                _digest(payload),
                expected_digest,
            ):
                raise ValueError("cache digest mismatch")

            result, cached_at, expires_at = (
                self._decode_payload(
                    payload,
                    provider_id=provider_id,
                    provider_version=provider_version,
                    request=request,
                )
            )

            status = (
                CacheLookupStatus.FRESH
                if self._validated_now() < expires_at
                else CacheLookupStatus.STALE
            )
            return CacheLookup(
                status=status,
                result=result,
                cached_at=cached_at,
                expires_at=expires_at,
            )
        except Exception as exc:
            return CacheLookup(
                status=CacheLookupStatus.INVALID,
                reason=type(exc).__name__,
            )

    def store(
        self,
        result: ExchangeSymbolResult,
        *,
        ttl: timedelta,
    ) -> Path:
        if ttl <= timedelta(0):
            raise ValueError("ttl must be > 0")
        if result.status is ExchangeProviderStatus.FAILED:
            raise ValueError(
                "FAILED provider results must not be cached"
            )
        if not result.listings:
            raise ValueError(
                "cached provider result requires listings"
            )

        cached_at = self._validated_now()
        expires_at = cached_at + ttl
        payload = self._encode_payload(
            result,
            cached_at=cached_at,
            expires_at=expires_at,
        )

        document = {
            "payload": payload,
            "integrity": {
                "algorithm": "SHA-256",
                "sha256": _digest(payload),
            },
        }

        path = self.path_for(
            provider_id=result.provider_id,
            provider_version=result.provider_version,
            exchange_code=result.request.exchange_code,
        )
        path.parent.mkdir(parents=True, exist_ok=True)

        encoded = (
            json.dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temporary_path, path)
            temporary_path = None
            return path
        finally:
            if (
                temporary_path is not None
                and temporary_path.exists()
            ):
                temporary_path.unlink()

    def _validated_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _encode_payload(
        result: ExchangeSymbolResult,
        *,
        cached_at: datetime,
        expires_at: datetime,
    ) -> dict[str, Any]:
        source_url = result.source_url
        if source_url and any(
            pattern in source_url.casefold()
            for pattern in _SECRET_TEXT_PATTERNS
        ):
            raise ValueError(
                "source_url contains secret-like text"
            )

        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "provider_id": result.provider_id,
            "provider_version": result.provider_version,
            "exchange_code": result.request.exchange_code,
            "cached_at": _format_datetime(cached_at),
            "expires_at": _format_datetime(expires_at),
            "result": {
                "source_name": result.source_name,
                "source_url": source_url,
                "status": result.status.value,
                "fetched_at": _format_datetime(
                    result.fetched_at
                ),
                "listings": [
                    {
                        "symbol": item.symbol,
                        "exchange": item.exchange,
                        "market": item.market,
                        "region": item.region,
                        "currency": item.currency,
                        "instrument_type": (
                            item.instrument_type
                        ),
                        "isin": item.isin,
                        "name": item.name,
                        "country": item.country,
                        "sector": item.sector,
                        "source": item.source,
                    }
                    for item in result.listings
                ],
                "diagnostics": [
                    {
                        "code": item.code,
                        "message": item.message,
                        "symbol": item.symbol,
                    }
                    for item in result.diagnostics
                ],
                "metadata": _json_safe(result.metadata),
            },
        }

    @staticmethod
    def _decode_payload(
        payload: Mapping[str, Any],
        *,
        provider_id: str,
        provider_version: str,
        request: ExchangeSymbolRequest,
    ) -> tuple[
        ExchangeSymbolResult,
        datetime,
        datetime,
    ]:
        schema_version = payload.get("schema_version")
        if schema_version != CACHE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported cache schema version"
            )

        cached_provider_id = _required_string(
            payload.get("provider_id"),
            "provider_id",
        )
        cached_provider_version = _required_string(
            payload.get("provider_version"),
            "provider_version",
        )
        cached_exchange = _required_string(
            payload.get("exchange_code"),
            "exchange_code",
        ).upper()

        if cached_provider_id != provider_id:
            raise ValueError("cache provider_id mismatch")
        if cached_provider_version != provider_version:
            raise ValueError(
                "cache provider_version mismatch"
            )
        if cached_exchange != request.exchange_code:
            raise ValueError("cache exchange mismatch")

        cached_at = _parse_datetime(
            payload.get("cached_at"),
            "cached_at",
        )
        expires_at = _parse_datetime(
            payload.get("expires_at"),
            "expires_at",
        )
        if expires_at <= cached_at:
            raise ValueError(
                "expires_at must be after cached_at"
            )

        raw_result = _required_mapping(
            payload.get("result"),
            "result",
        )

        raw_listings = raw_result.get("listings")
        if not isinstance(raw_listings, list):
            raise ValueError("result.listings must be an array")

        listings = tuple(
            RawMarketListing(
                symbol=_required_string(
                    item.get("symbol"),
                    "listing.symbol",
                ),
                exchange=_required_string(
                    item.get("exchange"),
                    "listing.exchange",
                ),
                market=_required_string(
                    item.get("market"),
                    "listing.market",
                ),
                region=_required_string(
                    item.get("region"),
                    "listing.region",
                ),
                currency=_optional_string(
                    item.get("currency"),
                    "listing.currency",
                ),
                instrument_type=_optional_string(
                    item.get("instrument_type"),
                    "listing.instrument_type",
                ),
                isin=_optional_string(
                    item.get("isin"),
                    "listing.isin",
                ),
                name=_optional_string(
                    item.get("name"),
                    "listing.name",
                ),
                country=_optional_string(
                    item.get("country"),
                    "listing.country",
                ),
                sector=_optional_string(
                    item.get("sector"),
                    "listing.sector",
                ),
                source=_optional_string(
                    item.get("source"),
                    "listing.source",
                ),
            )
            for item in (
                _required_mapping(value, "listing")
                for value in raw_listings
            )
        )

        raw_diagnostics = raw_result.get("diagnostics")
        if not isinstance(raw_diagnostics, list):
            raise ValueError(
                "result.diagnostics must be an array"
            )

        diagnostics = tuple(
            ExchangeProviderDiagnostic(
                code=_required_string(
                    item.get("code"),
                    "diagnostic.code",
                ),
                message=_required_string(
                    item.get("message"),
                    "diagnostic.message",
                ),
                symbol=_optional_string(
                    item.get("symbol"),
                    "diagnostic.symbol",
                ),
            )
            for item in (
                _required_mapping(value, "diagnostic")
                for value in raw_diagnostics
            )
        )

        metadata = raw_result.get("metadata")
        metadata = _required_mapping(
            metadata,
            "result.metadata",
        )
        safe_metadata = _json_safe(metadata)

        result = ExchangeSymbolResult(
            provider_id=cached_provider_id,
            provider_version=cached_provider_version,
            source_name=_required_string(
                raw_result.get("source_name"),
                "result.source_name",
            ),
            source_url=_optional_string(
                raw_result.get("source_url"),
                "result.source_url",
            ),
            request=request,
            status=ExchangeProviderStatus(
                _required_string(
                    raw_result.get("status"),
                    "result.status",
                )
            ),
            listings=listings,
            diagnostics=diagnostics,
            fetched_at=_parse_datetime(
                raw_result.get("fetched_at"),
                "result.fetched_at",
            ),
            metadata=dict(safe_metadata),
        )

        if result.status is ExchangeProviderStatus.FAILED:
            raise ValueError(
                "FAILED result must not exist in cache"
            )

        return result, cached_at, expires_at
