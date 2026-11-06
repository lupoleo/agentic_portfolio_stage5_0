from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest

from app.scanner.listing_start_reference import (
    ListingStartReferenceRegistry,
    SCHEMA,
)
from app.scanner.universe_models import MarketListing


NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)


def reference_document(
    symbol="NEW",
    exchange="XETRA",
    first="2026-09-15",
    event="IPO",
):
    # Synthetic evidence only, not an actual issuer source.
    return {
        "schema": SCHEMA,
        "references": [
            {
                "exchange": exchange,
                "symbol": symbol,
                "issuer_name": "Synthetic Example",
                "isin": None,
                "first_session": first,
                "event_kind": event,
                "ipo_confirmed": event == "IPO",
                "date_status": "CONFIRMED_TRADING_START",
                "source_kind": "EXCHANGE",
                "source_url": "https://example.org/synthetic-fixture",
                "source_title": "Synthetic trading-start confirmation",
                "source_excerpt": "Synthetic fixture: trading started.",
                "observed_at": NOW.isoformat(),
                "reviewed_at": NOW.isoformat(),
                "reviewed_by": "offline-test-fixture",
                "review_status": "APPROVED",
            }
        ],
    }


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.listing = MarketListing(
            "NEW", "XETRA", "XETRA", "EUROPE", currency="EUR"
        )

    def test_matched_evidence_has_exact_key_and_audit_hash(self):
        result = ListingStartReferenceRegistry(
            reference_document()
        ).resolve(self.listing, as_of=NOW)

        self.assertEqual(result.status, "MATCHED")
        self.assertEqual(result.evidence.listing_key, self.listing.key)
        self.assertEqual(result.evidence.event_kind, "IPO")
        self.assertIn(
            result.reference.record_sha256,
            result.evidence.source,
        )
        self.assertEqual(result.evidence.known_at, NOW)

    def test_no_symbol_or_cross_venue_fallback(self):
        listing = MarketListing("NEW", "NYSE", "NYSE", "US")
        result = ListingStartReferenceRegistry(
            reference_document()
        ).resolve(listing, as_of=NOW)

        self.assertEqual(result.status, "MISSING")
        self.assertIsNone(result.evidence)

    def test_no_historical_lookahead(self):
        result = ListingStartReferenceRegistry(
            reference_document()
        ).resolve(
            self.listing,
            as_of=NOW - timedelta(seconds=1),
        )

        self.assertEqual(result.status, "NOT_YET_KNOWN")
        self.assertIsNone(result.evidence)

    def test_isin_conflict(self):
        document = reference_document()
        document["references"][0]["isin"] = "REFERENCE-ID"

        listing = MarketListing(
            "NEW", "XETRA", "XETRA", "EUROPE", isin="OTHER-ID"
        )
        result = ListingStartReferenceRegistry(document).resolve(
            listing,
            as_of=NOW,
        )

        self.assertEqual(result.status, "CONFLICT")
        self.assertIsNone(result.evidence)

    def test_listing_start_not_promoted_to_ipo(self):
        result = ListingStartReferenceRegistry(
            reference_document(event="LISTING_START")
        ).resolve(self.listing, as_of=NOW)

        self.assertEqual(result.evidence.event_kind, "LISTING_START")

    def test_reject_weak_unreviewed_forecast_or_invalid_records(self):
        changes = [
            {"source_kind": "YAHOO_FIRST_BAR"},
            {"review_status": "PENDING"},
            {"date_status": "EXPECTED"},
            {"ipo_confirmed": False},
            {"source_excerpt": ""},
            {"source_url": "http://example.org"},
            {"source_url": "https://user:secret@example.org"},
            {"first_session": "2026-09-23"},
            {"reviewed_at": "2026-09-21T12:00:00+00:00"},
            {"observed_at": "2026-09-22T12:00:00"},
            {"symbol": "new"},
        ]

        for change in changes:
            document = reference_document()
            document["references"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                ListingStartReferenceRegistry(document)

    def test_duplicate_reference_rejected_even_if_identical(self):
        document = reference_document()
        document["references"].append(
            deepcopy(document["references"][0])
        )

        with self.assertRaises(ValueError):
            ListingStartReferenceRegistry(document)

    def test_duplicate_json_fields_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "r.json"
            path.write_text(
                '{"schema":"x","schema":"y","references":[]}',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                ListingStartReferenceRegistry.load(path)

    def test_record_hash_changes_with_review_or_source(self):
        document = reference_document()
        before = ListingStartReferenceRegistry(document).resolve(
            self.listing,
            as_of=NOW,
        )

        document["references"][0]["source_excerpt"] = (
            "Different supporting excerpt"
        )
        after = ListingStartReferenceRegistry(document).resolve(
            self.listing,
            as_of=NOW,
        )

        self.assertNotEqual(
            before.reference.record_sha256,
            after.reference.record_sha256,
        )
