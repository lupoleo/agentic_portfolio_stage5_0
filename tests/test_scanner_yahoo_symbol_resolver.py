from dataclasses import FrozenInstanceError
import unittest

from app.scanner.market_data_contracts import SymbolMappingStatus as Status
from app.scanner.universe_models import ListingKey, MarketListing
from app.scanner.yahoo_symbol_resolver import (
    YahooSymbolOverride, YahooSymbolResolver, YAHOO_VENUE_SUFFIXES,
)


class YahooSymbolResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = YahooSymbolResolver()

    def test_european_candidate_suffixes(self):
        cases = {
            "AS": ("ASML", "ASML.AS"), "AT": ("OPAP", "OPAP.AT"),
            "BIT": ("A2A", "A2A.MI"), "BR": ("ABI", "ABI.BR"),
            "BUD": ("OTP", "OTP.BD"), "CO": ("NOVO-B", "NOVO-B.CO"),
            "HE": ("NOKIA", "NOKIA.HE"), "LS": ("EDP", "EDP.LS"),
            "LSE": ("SHEL", "SHEL.L"), "MC": ("SAN", "SAN.MC"),
            "OL": ("EQNR", "EQNR.OL"), "PA": ("AIR", "AIR.PA"),
            "PR": ("CEZ", "CEZ.PR"), "ST": ("VOLV-B", "VOLV-B.ST"),
            "SW": ("NESN", "NESN.SW"), "VI": ("OMV", "OMV.VI"),
            "WAR": ("PKN", "PKN.WA"), "XETRA": ("SAP", "SAP.DE"),
        }
        for venue, (symbol, expected) in cases.items():
            with self.subTest(venue=venue):
                result = self.resolver.resolve(ListingKey(venue, symbol))
                self.assertEqual(result.status, Status.RESOLVED)
                self.assertEqual(result.resolved_symbol, expected)
                self.assertEqual(result.provider_id, "yahoo")
                self.assertTrue(result.rule_id)

    def test_five_us_venues_remain_unsuffixed(self):
        for venue in ("AMEX", "BATS", "NASDAQ", "NYSE", "NYSE ARCA"):
            with self.subTest(venue=venue):
                result = self.resolver.resolve(ListingKey(venue, "ABC"))
                self.assertEqual(result.resolved_symbol, "ABC")
                self.assertEqual(result.listing_key.exchange, venue)

    def test_us_share_class_translation_is_scoped(self):
        self.assertEqual(self.resolver.resolve(ListingKey("NYSE", "BRK.B"))
                         .resolved_symbol, "BRK-B")
        self.assertEqual(self.resolver.resolve(ListingKey("LSE", "BRK.B"))
                         .status, Status.UNMAPPED)

    def test_nordic_class_separators(self):
        for venue, symbol, expected in (
            ("ST", "VOLV B", "VOLV-B.ST"), ("ST", "VOLV.B", "VOLV-B.ST"),
            ("CO", "NOVO B", "NOVO-B.CO"), ("CO", "NOVO.B", "NOVO-B.CO"),
        ):
            with self.subTest(venue=venue, symbol=symbol):
                self.assertEqual(self.resolver.resolve(ListingKey(venue, symbol))
                                 .resolved_symbol, expected)

    def test_special_syntax_is_not_guessed(self):
        for symbol in ("BT.", "BT.A", "ABC/DEF", "ABC&DEF", "ABC%20", "ABC B"):
            with self.subTest(symbol=symbol):
                result = self.resolver.resolve(ListingKey("LSE", symbol))
                self.assertEqual(result.status, Status.UNMAPPED)
                self.assertEqual(result.diagnostics[0].code, "UNSUPPORTED_SYMBOL_FORMAT")

    def test_pre_suffixed_input_requires_override(self):
        result = self.resolver.resolve(ListingKey("BIT", "A2A.MI"))
        self.assertEqual(result.status, Status.UNMAPPED)
        self.assertIsNone(result.resolved_symbol)

    def test_unsupported_venues_never_fall_back(self):
        for venue in ("RO", "US", "F", "OTCQX", "UNKNOWN"):
            with self.subTest(venue=venue):
                result = self.resolver.resolve(ListingKey(venue, "SAP"))
                self.assertEqual(result.status, Status.UNMAPPED)
                self.assertEqual(result.diagnostics[0].code, "UNSUPPORTED_VENUE")

    def test_listing_is_preserved_and_does_not_need_isin(self):
        listing = MarketListing("SAP", "XETRA", "XETRA", "EUROPE",
                                currency="EUR", name="SAP SE", isin=None)
        original = repr(listing)
        result = self.resolver.resolve(listing)
        self.assertEqual(result.listing_key, listing.key)
        self.assertEqual(repr(listing), original)
        self.assertEqual(result.resolved_symbol, "SAP.DE")
        self.assertFalse(hasattr(result, "identity_status"))

    def test_override_precedes_rule_and_records_provenance(self):
        override = YahooSymbolOverride(ListingKey("LSE", "BT.A"),
                                       ("BT-A.L",), "test-fixture", "Class mapping")
        result = YahooSymbolResolver([override]).resolve(override.listing_key)
        self.assertEqual(result.resolved_symbol, "BT-A.L")
        self.assertEqual(result.rule_id, "exact-key-override-v1")
        self.assertIn("test-fixture", result.diagnostics[0].message)
        self.assertEqual(YahooSymbolResolver([override]).resolve(ListingKey("NYSE", "BT.A"))
                         .resolved_symbol, "BT-A")

    def test_override_can_block_or_preserve_ambiguity(self):
        key = ListingKey("LSE", "ABC")
        for candidates, expected in (((), Status.UNMAPPED),
                                     (("ABC.L", "DEF.L"), Status.AMBIGUOUS)):
            with self.subTest(candidates=candidates):
                override = YahooSymbolOverride(key, candidates, "fixture", "Uncertain")
                result = YahooSymbolResolver([override]).resolve(key)
                self.assertEqual(result.status, expected)
                self.assertIsNone(result.resolved_symbol)

    def test_override_foreign_venue_and_malformed_values_rejected(self):
        for venue, candidates in (("XETRA", ("SAP.F",)), ("NASDAQ", ("ABC.L",)),
                                  ("RO", ("TLV.RO",)), ("BIT", ("ABC.MI.MI",)),
                                  ("BIT", "ABC.MI"), ("BIT", ("../ABC.MI",))):
            with self.subTest(venue=venue, candidates=candidates):
                with self.assertRaises(ValueError):
                    YahooSymbolOverride(ListingKey(venue, "ABC"), candidates,
                                        "fixture", "Invalid")

    def test_duplicate_override_rejected(self):
        override = YahooSymbolOverride(ListingKey("NYSE", "ABC"), ("ABC",),
                                       "fixture", "Explicit")
        with self.assertRaises(ValueError):
            YahooSymbolResolver([override, override])

    def test_version_is_deterministic_and_content_sensitive(self):
        one = YahooSymbolOverride(ListingKey("NYSE", "ABC"), ("ABC",), "ref", "why")
        two = YahooSymbolOverride(ListingKey("NYSE", "DEF"), ("DEF",), "ref", "why")
        self.assertEqual(YahooSymbolResolver([one, two]).mapping_version,
                         YahooSymbolResolver([two, one]).mapping_version)
        self.assertNotEqual(self.resolver.mapping_version,
                            YahooSymbolResolver([one]).mapping_version)
        changed = YahooSymbolOverride(one.listing_key, ("XYZ",), "ref", "why")
        self.assertNotEqual(YahooSymbolResolver([one]).mapping_version,
                            YahooSymbolResolver([changed]).mapping_version)

    def test_configuration_is_immutable(self):
        with self.assertRaises(TypeError):
            YAHOO_VENUE_SUFFIXES["BIT"] = ".L"
        override = YahooSymbolOverride(ListingKey("NYSE", "ABC"), ("ABC",), "ref", "why")
        with self.assertRaises(FrozenInstanceError):
            override.reason = "changed"

    def test_batch_sorting_and_exact_key_deduplication(self):
        keys = [ListingKey("XETRA", "SAP"), ListingKey("BIT", "A2A")]
        results = self.resolver.resolve_many(keys + keys)
        self.assertEqual([r.listing_key for r in results], sorted(keys))
        self.assertEqual(results, self.resolver.resolve_many(reversed(keys)))

    def test_same_company_different_listing_stays_separate(self):
        results = self.resolver.resolve_many([ListingKey("XETRA", "SAP"),
                                               ListingKey("NYSE", "SAP")])
        self.assertEqual({r.resolved_symbol for r in results}, {"SAP", "SAP.DE"})

    def test_batch_symbol_collisions_block_all_involved_keys(self):
        for keys in ([ListingKey("NYSE", "ABC"), ListingKey("NASDAQ", "ABC")],
                     [ListingKey("NYSE", "BRK.B"), ListingKey("NYSE", "BRK-B")]):
            with self.subTest(keys=keys):
                for result in self.resolver.resolve_many(keys):
                    self.assertEqual(result.status, Status.UNMAPPED)
                    self.assertEqual(result.diagnostics[0].code, "YAHOO_SYMBOL_COLLISION")
                    self.assertIsNone(result.resolved_symbol)

    def test_wrong_python_input_is_explicit_error(self):
        with self.assertRaises(TypeError):
            self.resolver.resolve("SAP")


if __name__ == "__main__":
    unittest.main()
