from __future__ import annotations

import json
import os
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from app.scanner.eodhd_exchange_discovery import ExchangeDescriptor
from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.universe_models import RawMarketListing


EODHD_EXCHANGE_SYMBOLS_ENDPOINT = (
    "https://eodhd.com/api/exchange-symbol-list"
)
SOURCE_NAME = "EODHD Exchange Symbols API"
PROVIDER_ID = "eodhd-exchange-symbols"
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


def public_exchange_symbols_url(exchange_code: str) -> str:
    code = _required_text(exchange_code, "exchange_code").upper()
    return (
        f"{EODHD_EXCHANGE_SYMBOLS_ENDPOINT}/"
        f"{quote(code, safe='')}"
    )


def parse_exchange_symbol_row(
    payload: dict[str, Any],
    *,
    descriptor: ExchangeDescriptor,
    expected_exchange_code: str,
) -> RawMarketListing:
    symbol = _required_text(payload.get("Code"), "Code").upper()
    exchange = _required_text(
        payload.get("Exchange"),
        "Exchange",
    ).upper()
    expected = _required_text(
        expected_exchange_code,
        "expected_exchange_code",
    ).upper()

    if exchange != expected:
        raise ValueError(
            f"row exchange mismatch: expected {expected}, got {exchange}"
        )

    if descriptor.code != expected:
        raise ValueError(
            "descriptor/request exchange mismatch: "
            f"{descriptor.code} != {expected}"
        )

    if descriptor.is_virtual or descriptor.region is None:
        raise ValueError(
            f"exchange {descriptor.code} is not a physical classified exchange"
        )

    return RawMarketListing(
        symbol=symbol,
        exchange=exchange,
        market=descriptor.name,
        region=descriptor.region,
        currency=_required_text(
            payload.get("Currency"),
            "Currency",
        ),
        instrument_type=_required_text(
            payload.get("Type"),
            "Type",
        ),
        isin=_optional_text(payload.get("Isin"), "Isin"),
        name=_required_text(payload.get("Name"), "Name"),
        country=_required_text(payload.get("Country"), "Country"),
        source=SOURCE_NAME,
    )


class EODHDExchangeSymbolProvider(ExchangeSymbolProvider):
    provider_id = PROVIDER_ID
    provider_version = PROVIDER_VERSION

    def __init__(
        self,
        *,
        descriptor: ExchangeDescriptor,
        api_token: str | None = None,
        timeout_seconds: float = 30.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        token = (
            api_token
            if api_token is not None
            else os.getenv("EODHD_API_TOKEN")
        )
        token = token.strip() if isinstance(token, str) else ""

        if not token:
            raise ValueError(
                "EODHD API token missing; set EODHD_API_TOKEN "
                "in the process environment"
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if descriptor.is_virtual or descriptor.region is None:
            raise ValueError(
                "descriptor must represent a physical classified exchange"
            )

        self._descriptor = descriptor
        self._api_token = token
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    def fetch(
        self,
        request: ExchangeSymbolRequest,
    ) -> ExchangeSymbolResult:
        if request.exchange_code != self._descriptor.code:
            return self._failed(
                request,
                "EXCHANGE_DESCRIPTOR_MISMATCH",
                "Request exchange does not match provider descriptor: "
                f"{request.exchange_code} != {self._descriptor.code}",
            )

        public_url = public_exchange_symbols_url(
            request.exchange_code
        )
        request_url = public_url + "?" + urlencode(
            {
                "api_token": self._api_token,
                "fmt": "json",
            }
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
                f"EODHD exchange-symbol-list returned HTTP {exc.code}",
            )

        except (TimeoutError, socket.timeout):
            return self._failed(
                request,
                "TIMEOUT",
                "EODHD exchange-symbol-list timed out",
            )

        except URLError as exc:
            if isinstance(
                exc.reason,
                (TimeoutError, socket.timeout),
            ):
                return self._failed(
                    request,
                    "TIMEOUT",
                    "EODHD exchange-symbol-list timed out",
                )

            return self._failed(
                request,
                "NETWORK_ERROR",
                "EODHD exchange-symbol-list network error: "
                f"{type(exc.reason).__name__}",
            )

        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._failed(
                request,
                "INVALID_JSON",
                "EODHD exchange-symbol-list returned invalid JSON: "
                f"{type(exc).__name__}",
            )

        except Exception as exc:
            return self._failed(
                request,
                "FETCH_FAILED",
                "EODHD exchange-symbol-list fetch failed: "
                f"{type(exc).__name__}",
            )

        if not isinstance(payload, list):
            return self._failed(
                request,
                "INVALID_PAYLOAD",
                "EODHD exchange-symbol-list payload must be a JSON array",
            )

        diagnostics: list[ExchangeProviderDiagnostic] = []
        by_symbol: dict[str, RawMarketListing] = {}
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
                listing = parse_exchange_symbol_row(
                    row,
                    descriptor=self._descriptor,
                    expected_exchange_code=request.exchange_code,
                )
            except ValueError as exc:
                raw_symbol = row.get("Code")
                diagnostics.append(
                    ExchangeProviderDiagnostic(
                        code="ROW_SKIPPED",
                        message=f"Row {index} rejected: {exc}",
                        symbol=(
                            raw_symbol
                            if isinstance(raw_symbol, str)
                            else None
                        ),
                    )
                )
                continue

            previous = by_symbol.get(listing.symbol)

            if previous is None:
                by_symbol[listing.symbol] = listing
            elif previous == listing:
                duplicate_row_count += 1
            else:
                return self._failed(
                    request,
                    "CONFLICTING_DUPLICATE_SYMBOL",
                    "Conflicting duplicate EODHD symbol: "
                    f"{listing.symbol}",
                    symbol=listing.symbol,
                )

        listings = tuple(
            sorted(
                by_symbol.values(),
                key=lambda item: item.symbol,
            )
        )

        if not listings:
            return self._failed(
                request,
                "NO_VALID_LISTINGS",
                "EODHD exchange-symbol-list contained no valid listings",
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
                "exchange_code": request.exchange_code,
                "raw_row_count": len(payload),
                "listing_count": len(listings),
                "rejected_row_count": len(diagnostics),
                "duplicate_row_count": duplicate_row_count,
                "response_bytes": len(raw),
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
            source_url=public_exchange_symbols_url(
                request.exchange_code
            ),
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
