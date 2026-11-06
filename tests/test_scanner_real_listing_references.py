from datetime import datetime, timezone
import json
from pathlib import Path

from app.scanner.listing_start_reference import ListingStartReferenceRegistry
from app.scanner.universe_models import MarketListing


REFERENCE_PATH = Path("config/scanner/listing_start_references_v1.json")


def _registry():
    return ListingStartReferenceRegistry(
        json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    )


def test_reviewed_real_ipo_references_resolve_exact_listing_identity():
    registry = _registry()
    as_of = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    cases = (
        MarketListing(
            "SPCX",
            "NASDAQ",
            "NASDAQ",
            "US",
            "USD",
            "COMMON STOCK",
            "US84615Q1031",
        ),
        MarketListing(
            "LYNX",
            "NYSE",
            "NYSE",
            "US",
            "USD",
            "COMMON STOCK",
        ),
    )
    for listing in cases:
        result = registry.resolve(listing, as_of=as_of)
        assert result.status == "MATCHED"
        assert result.evidence is not None
        assert result.evidence.event_kind == "IPO"


def test_space_x_reference_does_not_transfer_to_other_listing_venues():
    registry = _registry()
    as_of = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    vienna = MarketListing(
        "SPCX",
        "VI",
        "VI",
        "EUROPE",
        "EUR",
        "COMMON STOCK",
        "US84615Q1031",
    )
    xetra = MarketListing(
        "SPX",
        "XETRA",
        "XETRA",
        "EUROPE",
        "EUR",
        "COMMON STOCK",
        "US84615Q1031",
    )
    assert registry.resolve(vienna, as_of=as_of).status == "MISSING"
    assert registry.resolve(xetra, as_of=as_of).status == "MISSING"


def test_reference_document_contains_only_expected_listing_keys():
    document = json.loads(
        REFERENCE_PATH.read_text(encoding="utf-8")
    )
    keys = {
        (item["exchange"], item["symbol"])
        for item in document["references"]
    }
    assert keys == {
        ("NASDAQ", "SPCX"),
        ("NYSE", "LYNX"),
    }