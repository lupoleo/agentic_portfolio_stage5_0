from __future__ import annotations

import json
import os
import socket
from collections import Counter
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.providers.eodhd_exchange_symbols import (
    public_exchange_symbols_url,
)
from app.scanner.universe_models import RawMarketListing


REQUEST_SCOPE = "US"
SOURCE_NAME = "EODHD US Aggregate Symbols API"
PROVIDER_ID = "eodhd-us-aggregate-symbols"
PROVIDER_VERSION = "1"


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    normalized = value.strip()
    return normalized or None


def parse_us_aggregate_row(payload: dict[str, Any]) -> RawMarketListing:
    """Map one EODHD US aggregate row without applying venue policy."""
    symbol = _required_text(payload.get("Code"), "Code").upper()
    exchange = _required_text(payload.get("Exchange"), "Exchange").upper()

    return RawMarketListing(
        symbol=symbol,
        exchange=exchange,
        market=exchange,
        region="US",
        currency=_required_text(payload.get("Currency"), "Currency"),
        instrument_type=_required_text(payload.get("Type"), "Type"),
        isin=_optional_text(payload.get("Isin"), "Isin"),
        name=_required_text(payload.get("Name"), "Name"),
        country=_required_text(payload.get("Country"), "Country"),
        source=SOURCE_NAME,
    )


class EODHDUSAggregateProvider(ExchangeSymbolProvider):
    """Acquire all provider-native venues returned by EODHD scope ``US``."""

    provider_id = PROVIDER_ID
    provider_version = PROVIDER_VERSION

    def __init__(
        self,
        *,
        api_token: str | None = None,
        timeout_seconds: float = 60.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        token = api_token if api_token is not None else os.getenv("EODHD_API_TOKEN")
        token = token.strip() if isinstance(token, str) else ""
        if not token:
            raise ValueError(
                "EODHD API token missing; set EODHD_API_TOKEN "
                "in the process environment"
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self._api_token = token
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    def fetch(self, request: ExchangeSymbolRequest) -> ExchangeSymbolResult:
        if request.exchange_code != REQUEST_SCOPE:
            return self._failed(
                request,
                "UNSUPPORTED_REQUEST_SCOPE",
                "EODHD US aggregate provider only supports request scope US: "
                f"got {request.exchange_code}",
            )

        public_url = public_exchange_symbols_url(REQUEST_SCOPE)
        request_url = public_url + "?" + urlencode(
            {"api_token": self._api_token, "fmt": "json"}
        )

        try:
            with self._opener(
                request_url,
                timeout=self._timeout_seconds,
            ) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            return self._failed(
                request,
                "HTTP_ERROR",
                f"EODHD US aggregate returned HTTP {exc.code}",
            )
        except (TimeoutError, socket.timeout):
            return self._failed(
                request,
                "TIMEOUT",
                "EODHD US aggregate timed out",
            )
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return self._failed(
                    request,
                    "TIMEOUT",
                    "EODHD US aggregate timed out",
                )
            return self._failed(
                request,
                "NETWORK_ERROR",
                "EODHD US aggregate network error: "
                f"{type(exc.reason).__name__}",
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._failed(
                request,
                "INVALID_JSON",
                "EODHD US aggregate returned invalid JSON: "
                f"{type(exc).__name__}",
            )
        except Exception as exc:
            return self._failed(
                request,
                "FETCH_FAILED",
                "EODHD US aggregate fetch failed: "
                f"{type(exc).__name__}",
            )

        if not isinstance(payload, list):
            return self._failed(
                request,
                "INVALID_PAYLOAD",
                "EODHD US aggregate payload must be a JSON array",
            )

        diagnostics: list[ExchangeProviderDiagnostic] = []
        by_key: dict[tuple[str, str], RawMarketListing] = {}
        duplicate_row_count = 0

        for index, row in enumerate(payload):
            if not isinstance(row, dict):
                diagnostics.append(
                    ExchangeProviderDiagnostic(
                        code="ROW_SKIPPED",
                        message=f"Row {index} is not a JSON object",
                    )
                )
                continue

            try:
                listing = parse_us_aggregate_row(row)
            except ValueError as exc:
                raw_symbol = row.get("Code")
                diagnostics.append(
                    ExchangeProviderDiagnostic(
                        code="ROW_SKIPPED",
                        message=f"Row {index} rejected: {exc}",
                        symbol=raw_symbol if isinstance(raw_symbol, str) else None,
                    )
                )
                continue

            key = (listing.exchange, listing.symbol)
            previous = by_key.get(key)
            if previous is None:
                by_key[key] = listing
            elif previous == listing:
                duplicate_row_count += 1
            else:
                return self._failed(
                    request,
                    "CONFLICTING_DUPLICATE_LISTING",
                    "Conflicting duplicate EODHD US listing: "
                    f"{listing.exchange}:{listing.symbol}",
                    symbol=listing.symbol,
                )

        listings = tuple(
            sorted(
                by_key.values(),
                key=lambda item: (item.exchange, item.symbol),
            )
        )
        if not listings:
            return self._failed(
                request,
                "NO_VALID_LISTINGS",
                "EODHD US aggregate contained no valid listings",
            )

        venue_counts = Counter(item.exchange for item in listings)
        instrument_type_counts = Counter(
            item.instrument_type or "<MISSING>" for item in listings
        )
        status = (
            ExchangeProviderStatus.PARTIAL
            if diagnostics
            else ExchangeProviderStatus.SUCCESS
        )

        return ExchangeSymbolResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_name=SOURCE_NAME,
            source_url=public_url,
            request=request,
            status=status,
            listings=listings,
            diagnostics=tuple(diagnostics),
            metadata={
                "request_scope": REQUEST_SCOPE,
                "raw_row_count": len(payload),
                "listing_count": len(listings),
                "rejected_row_count": len(diagnostics),
                "duplicate_row_count": duplicate_row_count,
                "response_bytes": len(raw),
                "venue_count": len(venue_counts),
                "venue_listing_counts": dict(sorted(venue_counts.items())),
                "instrument_type_counts": dict(
                    sorted(instrument_type_counts.items())
                ),
            },
        )

    def _failed(
        self,
        request: ExchangeSymbolRequest,
        code: str,
        message: str,
        *,
        symbol: str | None = None,
    ) -> ExchangeSymbolResult:
        return ExchangeSymbolResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_name=SOURCE_NAME,
            source_url=public_exchange_symbols_url(REQUEST_SCOPE),
            request=request,
            status=ExchangeProviderStatus.FAILED,
            diagnostics=(
                ExchangeProviderDiagnostic(
                    code=code,
                    message=message,
                    symbol=symbol,
                ),
            ),
        )
