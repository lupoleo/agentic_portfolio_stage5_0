from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


EODHD_EXCHANGES_LIST_ENDPOINT = "https://eodhd.com/api/exchanges-list/"
EODHD_PUBLIC_EXCHANGES_SOURCE_URL = EODHD_EXCHANGES_LIST_ENDPOINT

EUROPE_ISO2 = frozenset(
    {
        "AT", "BE", "CH", "CZ", "DE", "DK", "ES", "FI", "FR", "GB", "GR",
        "HU", "IE", "IS", "IT", "LT", "LU", "LV", "NL", "NO", "PL", "PT",
        "RO", "SE", "SK",
    }
)

# Asset-class containers documented by EODHD rather than physical equity venues.
EODHD_VIRTUAL_EXCHANGE_CODES = frozenset(
    {"CC", "FOREX", "MONEY", "GBOND", "EUFUND"}
)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _optional_upper(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional value must be a string or null")
    normalized = value.strip().upper()
    return normalized or None


class ExchangeDiscoveryStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ExchangeDiscoveryDiagnostic:
    code: str
    message: str
    exchange_code: str | None = None

    def __post_init__(self) -> None:
        code = _required_text(self.code, "diagnostic code").upper()
        message = _required_text(self.message, "diagnostic message")
        exchange_code = _optional_upper(self.exchange_code)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "exchange_code", exchange_code)


@dataclass(frozen=True)
class ExchangeDescriptor:
    """
    Canonical description of one EODHD exchange-list row.

    region is derived locally from ISO country metadata and is intentionally
    limited to the scanner regions currently in scope.
    """

    code: str
    name: str
    operating_mic: str | None
    country: str
    currency: str
    country_iso2: str | None
    country_iso3: str | None
    region: str | None
    is_virtual: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required_text(self.code, "code").upper())
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(self, "country", _required_text(self.country, "country"))
        object.__setattr__(self, "currency", _required_text(self.currency, "currency").upper())
        object.__setattr__(self, "operating_mic", _optional_upper(self.operating_mic))
        object.__setattr__(self, "country_iso2", _optional_upper(self.country_iso2))
        object.__setattr__(self, "country_iso3", _optional_upper(self.country_iso3))
        region = _optional_upper(self.region)
        if region not in {None, "US", "EUROPE", "OTHER"}:
            raise ValueError(f"unsupported region: {region!r}")
        object.__setattr__(self, "region", region)


@dataclass(frozen=True)
class ExchangeDiscoveryResult:
    provider_id: str
    provider_version: str
    source_name: str
    source_url: str
    status: ExchangeDiscoveryStatus
    exchanges: tuple[ExchangeDescriptor, ...] = ()
    diagnostics: tuple[ExchangeDiscoveryDiagnostic, ...] = ()
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider_id = _required_text(self.provider_id, "provider_id")
        provider_version = _required_text(self.provider_version, "provider_version")
        source_name = _required_text(self.source_name, "source_name")
        source_url = _required_text(self.source_url, "source_url")

        if "api_token=" in source_url.lower():
            raise ValueError("source_url must not contain an API token")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")

        if self.status == ExchangeDiscoveryStatus.SUCCESS and not self.exchanges:
            raise ValueError("SUCCESS requires at least one exchange")
        if self.status == ExchangeDiscoveryStatus.PARTIAL:
            if not self.exchanges:
                raise ValueError("PARTIAL requires at least one exchange")
            if not self.diagnostics:
                raise ValueError("PARTIAL requires diagnostics")
        if self.status == ExchangeDiscoveryStatus.FAILED:
            if self.exchanges:
                raise ValueError("FAILED must not contain exchanges")
            if not self.diagnostics:
                raise ValueError("FAILED requires diagnostics")

        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "provider_version", provider_version)
        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "source_url", source_url)


def classify_exchange_region(
    *,
    code: str,
    country_iso2: str | None,
) -> str | None:
    canonical_code = _required_text(code, "code").upper()
    iso2 = _optional_upper(country_iso2)

    if canonical_code in EODHD_VIRTUAL_EXCHANGE_CODES:
        return None
    if iso2 == "US":
        return "US"
    if iso2 in EUROPE_ISO2:
        return "EUROPE"
    return "OTHER"


def parse_exchange_descriptor(payload: dict[str, Any]) -> ExchangeDescriptor:
    code = _required_text(payload.get("Code"), "Code").upper()
    name = _required_text(payload.get("Name"), "Name")
    country = _required_text(payload.get("Country"), "Country")
    currency = _required_text(payload.get("Currency"), "Currency").upper()
    operating_mic = _optional_upper(payload.get("OperatingMIC"))
    iso2 = _optional_upper(payload.get("CountryISO2"))
    iso3 = _optional_upper(payload.get("CountryISO3"))

    return ExchangeDescriptor(
        code=code,
        name=name,
        operating_mic=operating_mic,
        country=country,
        currency=currency,
        country_iso2=iso2,
        country_iso3=iso3,
        region=classify_exchange_region(code=code, country_iso2=iso2),
        is_virtual=code in EODHD_VIRTUAL_EXCHANGE_CODES,
    )


class EODHDExchangeDiscoveryProvider:
    provider_id = "eodhd-exchange-discovery"
    provider_version = "1"

    def __init__(
        self,
        *,
        api_token: str | None = None,
        timeout_seconds: float = 20.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        token = api_token if api_token is not None else os.getenv("EODHD_API_TOKEN")
        token = token.strip() if isinstance(token, str) else ""
        if not token:
            raise ValueError(
                "EODHD API token missing; set EODHD_API_TOKEN in the process environment"
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self._api_token = token
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    def discover(self) -> ExchangeDiscoveryResult:
        request_url = (
            f"{EODHD_EXCHANGES_LIST_ENDPOINT}?"
            + urlencode({"api_token": self._api_token})
        )

        try:
            with self._opener(request_url, timeout=self._timeout_seconds) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            return self._failed(
                "HTTP_ERROR",
                f"EODHD exchanges-list returned HTTP {exc.code}",
            )
        except URLError as exc:
            return self._failed(
                "NETWORK_ERROR",
                f"EODHD exchanges-list network error: {exc.reason}",
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._failed(
                "INVALID_JSON",
                f"EODHD exchanges-list returned invalid JSON: {type(exc).__name__}",
            )
        except Exception as exc:
            return self._failed(
                "FETCH_FAILED",
                f"EODHD exchanges-list fetch failed: {type(exc).__name__}",
            )

        if not isinstance(payload, list):
            return self._failed(
                "INVALID_PAYLOAD",
                "EODHD exchanges-list payload must be a JSON array",
            )

        exchanges: list[ExchangeDescriptor] = []
        diagnostics: list[ExchangeDiscoveryDiagnostic] = []

        for index, row in enumerate(payload):
            if not isinstance(row, dict):
                diagnostics.append(
                    ExchangeDiscoveryDiagnostic(
                        code="ROW_SKIPPED",
                        message=f"Row {index} is not a JSON object",
                    )
                )
                continue
            try:
                exchanges.append(parse_exchange_descriptor(row))
            except ValueError as exc:
                code = row.get("Code")
                diagnostics.append(
                    ExchangeDiscoveryDiagnostic(
                        code="ROW_SKIPPED",
                        message=f"Row {index} rejected: {exc}",
                        exchange_code=code if isinstance(code, str) else None,
                    )
                )

        # Fail closed on duplicate provider exchange codes. They are API identity
        # keys and ambiguous duplicates would make later symbol acquisition unsafe.
        seen: set[str] = set()
        duplicate_codes: set[str] = set()
        for item in exchanges:
            if item.code in seen:
                duplicate_codes.add(item.code)
            seen.add(item.code)

        if duplicate_codes:
            return self._failed(
                "DUPLICATE_EXCHANGE_CODE",
                "Duplicate EODHD exchange codes: " + ", ".join(sorted(duplicate_codes)),
            )

        exchanges.sort(key=lambda item: item.code)

        if not exchanges:
            return self._failed(
                "NO_VALID_EXCHANGES",
                "EODHD exchanges-list contained no valid exchange rows",
            )

        status = (
            ExchangeDiscoveryStatus.PARTIAL
            if diagnostics
            else ExchangeDiscoveryStatus.SUCCESS
        )

        metadata = {
            "exchange_count": len(exchanges),
            "physical_exchange_count": sum(not item.is_virtual for item in exchanges),
            "virtual_exchange_count": sum(item.is_virtual for item in exchanges),
            "us_exchange_count": sum(item.region == "US" for item in exchanges),
            "europe_exchange_count": sum(item.region == "EUROPE" for item in exchanges),
            "other_exchange_count": sum(item.region == "OTHER" for item in exchanges),
            "unclassified_exchange_count": sum(item.region is None for item in exchanges),
        }

        return ExchangeDiscoveryResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_name="EODHD Exchanges API",
            source_url=EODHD_PUBLIC_EXCHANGES_SOURCE_URL,
            status=status,
            exchanges=tuple(exchanges),
            diagnostics=tuple(diagnostics),
            metadata=metadata,
        )

    def _failed(self, code: str, message: str) -> ExchangeDiscoveryResult:
        return ExchangeDiscoveryResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_name="EODHD Exchanges API",
            source_url=EODHD_PUBLIC_EXCHANGES_SOURCE_URL,
            status=ExchangeDiscoveryStatus.FAILED,
            diagnostics=(ExchangeDiscoveryDiagnostic(code=code, message=message),),
        )
