from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from app.scanner.market_data_contracts import (
    MarketDataAvailabilityStatus as Availability,
    MarketDataDiagnostic,
    MarketDataIdentityStatus as Identity,
    MarketDataVerification,
    SymbolMappingResult,
    SymbolMappingStatus as Mapping,
)
from app.scanner.universe_models import ListingKey


class MarketDataContractTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        self.diagnostic = MarketDataDiagnostic("TEST", "Test evidence")
        self.mapping = SymbolMappingResult(
            ListingKey("NYSE", "ABC"), "yahoo", "1", Mapping.RESOLVED,
            ("ABC",), "us-symbol-v1",
        )
        self.result = MarketDataVerification(
            mapping=self.mapping,
            identity_status=Identity.VERIFIED,
            availability_status=Availability.AVAILABLE,
            checked_at=self.now,
            expires_at=self.now + timedelta(hours=1),
            requested_start=self.now - timedelta(days=10),
            requested_end=self.now,
            verification_version="1",
            valid_bar_count=1,
            latest_bar_at=self.now - timedelta(days=1),
            identity_evidence=(("symbol", "ABC"), ("currency", "USD")),
        )

    def test_verified_available_is_ready(self):
        self.assertTrue(self.result.ready_for_market_data(as_of=self.now))

    def test_expiry_and_future_observations_fail_closed(self):
        for moment in (self.result.expires_at, self.now - timedelta(seconds=1)):
            with self.subTest(moment=moment):
                self.assertFalse(self.result.ready_for_market_data(as_of=moment))

    def test_prices_do_not_verify_identity(self):
        for status in (Identity.UNVERIFIED, Identity.MISMATCH):
            result = replace(self.result, identity_status=status,
                             diagnostics=(self.diagnostic,))
            self.assertFalse(result.ready_for_market_data(as_of=self.now))

    def test_unavailable_states_never_admit(self):
        for status in Availability:
            if status is Availability.AVAILABLE:
                continue
            with self.subTest(status=status):
                result = replace(self.result, availability_status=status,
                                 valid_bar_count=0, latest_bar_at=None,
                                 diagnostics=(self.diagnostic,))
                self.assertFalse(result.ready_for_market_data(as_of=self.now))

    def test_resolved_requires_single_candidate_and_rule(self):
        for fields in ({"candidate_symbols": ()},
                       {"candidate_symbols": ("ABC", "DEF")},
                       {"rule_id": None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(self.mapping, **fields)

    def test_ambiguous_has_no_selected_symbol(self):
        mapping = replace(self.mapping, status=Mapping.AMBIGUOUS,
                          candidate_symbols=("DEF", "ABC"),
                          diagnostics=(self.diagnostic,))
        self.assertIsNone(mapping.resolved_symbol)
        self.assertEqual(mapping.candidate_symbols, ("ABC", "DEF"))
        with self.assertRaises(ValueError):
            replace(self.result, mapping=mapping)

    def test_unknown_mapping_is_not_checked(self):
        mapping = replace(self.mapping, status=Mapping.UNMAPPED,
                          candidate_symbols=(), diagnostics=(self.diagnostic,))
        result = replace(self.result, mapping=mapping,
                         identity_status=Identity.UNVERIFIED,
                         availability_status=Availability.NOT_CHECKED,
                         valid_bar_count=0, latest_bar_at=None)
        self.assertFalse(result.ready_for_market_data(as_of=self.now))

    def test_available_requires_actual_bars(self):
        for fields in ({"valid_bar_count": 0}, {"latest_bar_at": None},
                       {"latest_bar_at": self.now + timedelta(days=1)},
                       {"valid_bar_count": True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(self.result, **fields)

    def test_evidence_and_diagnostics_required(self):
        with self.assertRaises(ValueError):
            replace(self.result, identity_evidence=())
        with self.assertRaises(ValueError):
            replace(self.result, availability_status=Availability.TEMPORARY_ERROR,
                    valid_bar_count=0, latest_bar_at=None)

    def test_timezone_and_interval_validation(self):
        for fields in ({"checked_at": self.now.replace(tzinfo=None)},
                       {"expires_at": self.now},
                       {"requested_start": self.now}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(self.result, **fields)

    def test_cache_does_not_change_gate_or_verification_time(self):
        cached = replace(self.result, from_cache=True)
        self.assertEqual(cached.checked_at, self.result.checked_at)
        self.assertTrue(cached.ready_for_market_data(as_of=self.now))
        self.assertFalse(cached.ready_for_market_data(as_of=cached.expires_at))

    def test_listing_identity_is_preserved(self):
        self.assertEqual(self.result.mapping.listing_key, ListingKey("NYSE", "ABC"))
        self.assertNotEqual(self.result.mapping.listing_key, ListingKey("NASDAQ", "ABC"))


if __name__ == "__main__":
    unittest.main()
