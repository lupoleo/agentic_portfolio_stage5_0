import pytest

from app.scanner.universe_models import (
    ListingKey,
    MarketListing,
    RawMarketListing,
)
from app.scanner.universe_provider import (
    build_canonical_universe,
    normalize_listing_symbol,
)


def raw(
    symbol,
    exchange,
    *,
    market="US",
    region="US",
    currency="USD",
    instrument_type="COMMON STOCK",
    isin=None,
    name=None,
    country=None,
    sector=None,
    source=None,
):
    return RawMarketListing(
        symbol=symbol,
        exchange=exchange,
        market=market,
        region=region,
        currency=currency,
        instrument_type=instrument_type,
        isin=isin,
        name=name,
        country=country,
        sector=sector,
        source=source,
    )


def test_normalizer_preserves_provider_native_class_share_syntax():
    assert normalize_listing_symbol(" brk.b ") == "BRK.B"


def test_listing_identity_is_exchange_plus_symbol():
    a = ListingKey("NYSE", "ABC")
    b = ListingKey("NASDAQ", "ABC")
    assert a != b


def test_deduplicates_same_listing_and_aggregates_sources():
    universe = build_canonical_universe(
        [
            raw("NVDA", "NASDAQ", source="EODHD"),
            raw("nvda", "NASDAQ", source="SECONDARY"),
        ]
    )
    assert len(universe) == 1
    assert universe[0].key == ListingKey("NASDAQ", "NVDA")
    assert universe[0].sources == ("EODHD", "SECONDARY")


def test_same_symbol_on_different_exchanges_is_not_deduplicated():
    universe = build_canonical_universe(
        [
            raw("ABC", "NYSE"),
            raw("ABC", "NASDAQ"),
        ]
    )
    assert len(universe) == 2
    assert [x.exchange for x in universe] == ["NASDAQ", "NYSE"]


def test_explicit_exclusion_targets_exact_listing():
    universe = build_canonical_universe(
        [
            raw("ABC", "NYSE"),
            raw("ABC", "NASDAQ"),
        ],
        exclusions=[ListingKey("NYSE", "ABC")],
    )
    assert [(x.exchange, x.symbol) for x in universe] == [("NASDAQ", "ABC")]


def test_enabled_exchange_filter_is_exchange_based():
    universe = build_canonical_universe(
        [
            raw("NVDA", "NASDAQ"),
            raw("SAP", "XETRA", market="GERMANY", region="EUROPE", currency="EUR"),
        ],
        enabled_exchanges=["XETRA"],
    )
    assert [(x.exchange, x.symbol) for x in universe] == [("XETRA", "SAP")]


def test_issuer_country_does_not_control_listing_eligibility():
    universe = build_canonical_universe(
        [
            raw(
                "TSM",
                "NYSE",
                country="TAIWAN",
                source="EODHD",
            )
        ]
    )
    assert len(universe) == 1
    assert universe[0].country == "TAIWAN"
    assert universe[0].exchange == "NYSE"


def test_eodhd_style_metadata_is_preserved():
    universe = build_canonical_universe(
        [
            raw(
                "SAP",
                "XETRA",
                market="GERMANY",
                region="EUROPE",
                currency="EUR",
                instrument_type="COMMON STOCK",
                isin="DE0007164600",
                name="SAP SE",
                country="GERMANY",
                source="EODHD",
            )
        ]
    )
    item = universe[0]
    assert item.currency == "EUR"
    assert item.instrument_type == "COMMON STOCK"
    assert item.isin == "DE0007164600"
    assert item.name == "SAP SE"
    assert item.sources == ("EODHD",)


def test_conflicting_identity_metadata_fails_closed():
    with pytest.raises(ValueError, match="conflicting currency"):
        build_canonical_universe(
            [
                raw("ABC", "NYSE", currency="USD"),
                raw("ABC", "NYSE", currency="EUR"),
            ]
        )


def test_descriptive_metadata_can_fill_missing_values():
    universe = build_canonical_universe(
        [
            raw("NVDA", "NASDAQ", name=None, sector=None),
            raw(
                "NVDA",
                "NASDAQ",
                name="NVIDIA Corporation",
                sector="Technology",
            ),
        ]
    )
    assert universe[0].name == "NVIDIA Corporation"
    assert universe[0].sector == "Technology"


def test_result_order_is_deterministic_by_exchange_then_symbol():
    universe = build_canonical_universe(
        [
            raw("MSFT", "NASDAQ"),
            raw("AAPL", "NASDAQ"),
            raw("IBM", "NYSE"),
        ]
    )
    assert [(x.exchange, x.symbol) for x in universe] == [
        ("NASDAQ", "AAPL"),
        ("NASDAQ", "MSFT"),
        ("NYSE", "IBM"),
    ]


def test_market_listing_has_no_index_membership_field():
    assert "memberships" not in MarketListing.__dataclass_fields__
