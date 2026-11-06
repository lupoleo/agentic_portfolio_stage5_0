from __future__ import annotations

from collections.abc import Iterable

from app.scanner.universe_models import (
    ListingKey,
    MarketListing,
    RawMarketListing,
)


def normalize_listing_symbol(raw_symbol: str) -> str:
    """
    Canonicalize provider-native listing codes only.

    Exchange-specific Yahoo mapping belongs to the later market-data adapter.
    In particular this function MUST NOT rewrite class-share syntax such as
    BRK.B -> BRK-B because that is a Yahoo representation concern.
    """
    symbol = raw_symbol.strip().upper()
    if not symbol:
        raise ValueError("raw_symbol must not be blank")
    return symbol


def build_canonical_universe(
    raw_listings: Iterable[RawMarketListing],
    *,
    exclusions: Iterable[ListingKey] = (),
    enabled_exchanges: Iterable[str] | None = None,
) -> list[MarketListing]:
    """
    Build the deterministic exchange-based scanner universe.

    Responsibilities:
    - preserve one record per exchange listing;
    - deduplicate by (exchange, symbol);
    - aggregate source provenance;
    - apply explicit listing exclusions;
    - optionally restrict to enabled exchanges;
    - fail closed on conflicting identity metadata.

    Not responsible for:
    - index memberships;
    - instrument-type eligibility policy;
    - Yahoo symbol translation;
    - market-data availability;
    - liquidity/history checks.
    """
    excluded = set(exclusions)

    allowed_exchanges: set[str] | None
    if enabled_exchanges is None:
        allowed_exchanges = None
    else:
        allowed_exchanges = {
            exchange.strip().upper()
            for exchange in enabled_exchanges
            if exchange and exchange.strip()
        }

    aggregated: dict[ListingKey, dict[str, object]] = {}

    for raw in raw_listings:
        symbol = normalize_listing_symbol(raw.symbol)
        key = ListingKey(exchange=raw.exchange, symbol=symbol)

        if allowed_exchanges is not None and key.exchange not in allowed_exchanges:
            continue
        if key in excluded:
            continue

        incoming = {
            "market": raw.market,
            "region": raw.region,
            "currency": raw.currency,
            "instrument_type": raw.instrument_type,
            "isin": raw.isin,
            "name": raw.name,
            "country": raw.country,
            "sector": raw.sector,
        }

        current = aggregated.get(key)
        if current is None:
            aggregated[key] = {
                **incoming,
                "sources": {raw.source} if raw.source else set(),
            }
            continue

        # Identity-critical metadata must never silently diverge for the same
        # exchange listing.
        for field in (
            "market",
            "region",
            "currency",
            "instrument_type",
            "isin",
        ):
            previous = current[field]
            value = incoming[field]
            if previous is not None and value is not None and previous != value:
                raise ValueError(
                    f"conflicting {field} for "
                    f"{key.exchange}:{key.symbol}: "
                    f"{previous!r} != {value!r}"
                )
            if previous is None and value is not None:
                current[field] = value

        # Descriptive metadata is opportunistic. Fill missing values, but do
        # not let presentation differences create false identity conflicts.
        for field in ("name", "country", "sector"):
            if current[field] is None and incoming[field] is not None:
                current[field] = incoming[field]

        sources = current["sources"]
        assert isinstance(sources, set)
        if raw.source:
            sources.add(raw.source)

    result = [
        MarketListing(
            symbol=key.symbol,
            exchange=key.exchange,
            market=str(data["market"]),
            region=str(data["region"]),
            currency=data["currency"] if isinstance(data["currency"], str) else None,
            instrument_type=(
                data["instrument_type"]
                if isinstance(data["instrument_type"], str)
                else None
            ),
            isin=data["isin"] if isinstance(data["isin"], str) else None,
            name=data["name"] if isinstance(data["name"], str) else None,
            country=data["country"] if isinstance(data["country"], str) else None,
            sector=data["sector"] if isinstance(data["sector"], str) else None,
            sources=tuple(data["sources"]),
        )
        for key, data in aggregated.items()
    ]

    return sorted(result, key=lambda item: (item.exchange, item.symbol))
