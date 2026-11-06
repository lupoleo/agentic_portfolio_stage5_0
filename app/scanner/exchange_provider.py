from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.scanner.universe_models import RawMarketListing


class ExchangeProviderStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ExchangeSymbolRequest:
    """
    Request all supported listings for one provider exchange code.
    """

    exchange_code: str

    def __post_init__(self) -> None:
        exchange_code = self.exchange_code.strip().upper()
        if not exchange_code:
            raise ValueError("exchange_code must not be blank")
        object.__setattr__(self, "exchange_code", exchange_code)


@dataclass(frozen=True)
class ExchangeProviderDiagnostic:
    code: str
    message: str
    symbol: str | None = None

    def __post_init__(self) -> None:
        code = self.code.strip().upper()
        message = self.message.strip()
        symbol = self.symbol.strip().upper() if self.symbol else None
        if not code:
            raise ValueError("diagnostic code must not be blank")
        if not message:
            raise ValueError("diagnostic message must not be blank")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "symbol", symbol)


@dataclass(frozen=True)
class ExchangeSymbolResult:
    """
    Auditable result for one exchange-symbol-list acquisition.
    """

    provider_id: str
    provider_version: str
    source_name: str
    source_url: str | None
    request: ExchangeSymbolRequest
    status: ExchangeProviderStatus
    listings: tuple[RawMarketListing, ...] = ()
    diagnostics: tuple[ExchangeProviderDiagnostic, ...] = ()
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip()
        provider_version = self.provider_version.strip()
        source_name = self.source_name.strip()
        source_url = self.source_url.strip() if self.source_url else None

        if not provider_id:
            raise ValueError("provider_id must not be blank")
        if not provider_version:
            raise ValueError("provider_version must not be blank")
        if not source_name:
            raise ValueError("source_name must not be blank")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")

        if self.status == ExchangeProviderStatus.SUCCESS and not self.listings:
            raise ValueError("SUCCESS requires at least one listing")
        if self.status == ExchangeProviderStatus.FAILED and self.listings:
            raise ValueError("FAILED must not contain listings")
        if self.status == ExchangeProviderStatus.FAILED and not self.diagnostics:
            raise ValueError("FAILED requires diagnostics")
        if self.status == ExchangeProviderStatus.PARTIAL:
            if not self.listings:
                raise ValueError("PARTIAL requires at least one listing")
            if not self.diagnostics:
                raise ValueError("PARTIAL requires diagnostics")

        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "provider_version", provider_version)
        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "source_url", source_url)


class ExchangeSymbolProvider(ABC):
    """
    I/O boundary for exchange-wide listing acquisition.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def provider_version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def fetch(
        self,
        request: ExchangeSymbolRequest,
    ) -> ExchangeSymbolResult:
        raise NotImplementedError
