from __future__ import annotations

from dataclasses import dataclass


def _required_upper(value: str, field_name: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _optional_upper(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True, order=True)
class ListingKey:
    """
    Stable V1 identity of a market listing.

    The same ticker code can legally exist on multiple exchanges, therefore
    exchange + symbol is the canonical deduplication/exclusion key.
    """

    exchange: str
    symbol: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "exchange",
            _required_upper(self.exchange, "exchange"),
        )
        object.__setattr__(
            self,
            "symbol",
            _required_upper(self.symbol, "symbol"),
        )


@dataclass(frozen=True)
class RawMarketListing:
    """
    Provider-facing exchange listing before canonical aggregation.

    This model intentionally contains no index-membership semantics.
    """

    symbol: str
    exchange: str
    market: str
    region: str
    currency: str | None = None
    instrument_type: str | None = None
    isin: str | None = None
    name: str | None = None
    country: str | None = None
    sector: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "symbol",
            _required_upper(self.symbol, "symbol"),
        )
        object.__setattr__(
            self,
            "exchange",
            _required_upper(self.exchange, "exchange"),
        )
        object.__setattr__(
            self,
            "market",
            _required_upper(self.market, "market"),
        )
        object.__setattr__(
            self,
            "region",
            _required_upper(self.region, "region"),
        )
        object.__setattr__(self, "currency", _optional_upper(self.currency))
        object.__setattr__(
            self,
            "instrument_type",
            _optional_upper(self.instrument_type),
        )
        object.__setattr__(self, "isin", _optional_upper(self.isin))
        object.__setattr__(self, "name", _optional_text(self.name))
        object.__setattr__(self, "country", _optional_upper(self.country))
        object.__setattr__(self, "sector", _optional_text(self.sector))
        object.__setattr__(self, "source", _optional_text(self.source))

    @property
    def key(self) -> ListingKey:
        return ListingKey(exchange=self.exchange, symbol=self.symbol)


@dataclass(frozen=True)
class MarketListing:
    """
    Canonical scanner listing.

    One record represents one tradable listing on one exchange. It is not an
    issuer/company identity and it is not an index membership.
    """

    symbol: str
    exchange: str
    market: str
    region: str
    currency: str | None = None
    instrument_type: str | None = None
    isin: str | None = None
    name: str | None = None
    country: str | None = None
    sector: str | None = None
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        symbol = _required_upper(self.symbol, "symbol")
        exchange = _required_upper(self.exchange, "exchange")
        market = _required_upper(self.market, "market")
        region = _required_upper(self.region, "region")
        sources = tuple(
            sorted(
                {
                    item.strip()
                    for item in self.sources
                    if item and item.strip()
                }
            )
        )

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "currency", _optional_upper(self.currency))
        object.__setattr__(
            self,
            "instrument_type",
            _optional_upper(self.instrument_type),
        )
        object.__setattr__(self, "isin", _optional_upper(self.isin))
        object.__setattr__(self, "name", _optional_text(self.name))
        object.__setattr__(self, "country", _optional_upper(self.country))
        object.__setattr__(self, "sector", _optional_text(self.sector))
        object.__setattr__(self, "sources", sources)

    @property
    def key(self) -> ListingKey:
        return ListingKey(exchange=self.exchange, symbol=self.symbol)
