from datetime import datetime, timedelta, timezone
import unittest

import pandas as pd

from app.scanner.market_data_contracts import (
    MarketDataAvailabilityStatus as A, MarketDataIdentityStatus as I,
)
from app.scanner.universe_models import MarketListing
from app.scanner.yahoo_symbol_resolver import YahooSymbolResolver
from app.scanner.yahoo_market_data_verifier import (
    YahooMarketDataVerifier, classify_exception,
)


class YFRateLimitError(Exception):
    pass


class YFPricesMissingError(Exception):
    pass


class YFTzMissingError(Exception):
    pass


class FakeTicker:
    def __init__(self, frame, metadata):
        self.frame, self.metadata = frame, metadata
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.frame, Exception):
            raise self.frame
        return self.frame

    def get_history_metadata(self):
        if isinstance(self.metadata, Exception):
            raise self.metadata
        return self.metadata


class YahooVerifierTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        self.start = self.now - timedelta(days=10)
        self.listing = MarketListing("SAP", "XETRA", "XETRA", "EUROPE",
                                     "EUR", "COMMON STOCK", name="SAP SE")
        self.metadata = {"symbol": "SAP.DE", "exchangeName": "GER",
                         "currency": "EUR", "instrumentType": "EQUITY"}
        self.frame = pd.DataFrame({"Open": [10., 11.], "High": [12., 13.],
                                   "Low": [9., 10.], "Close": [11., 12.],
                                   "Volume": [100., 200.]},
                                  index=pd.date_range("2026-09-17", periods=2, tz="Europe/Berlin"))
        self.ticker = FakeTicker(self.frame, self.metadata)
        self.verifier = YahooMarketDataVerifier(ticker_factory=lambda s: self.ticker,
                                                now=lambda: self.now)

    def run_verification(self, listing=None):
        listing = listing or self.listing
        return self.verifier.verify(listing, YahooSymbolResolver().resolve(listing),
                                    start=self.start, end=self.now)

    def test_verified_available_with_no_isin(self):
        result = self.run_verification()
        self.assertEqual(result.identity_status, I.VERIFIED)
        self.assertEqual(result.availability_status, A.AVAILABLE)
        self.assertEqual(result.valid_bar_count, 2)
        self.assertTrue(result.ready_for_market_data(as_of=self.now))

    def test_history_arguments_and_no_input_mutation(self):
        original = self.frame.copy(deep=True)
        self.run_verification()
        call = self.ticker.calls[0]
        self.assertTrue(call["raise_errors"])
        self.assertFalse(call["auto_adjust"])
        self.assertFalse(call["repair"])
        self.assertEqual(call["timeout"], 20.)
        pd.testing.assert_frame_equal(original, self.frame)

    def test_resolved_prices_do_not_compensate_for_missing_metadata(self):
        for field in ("symbol", "exchangeName", "currency", "instrumentType"):
            with self.subTest(field=field):
                self.ticker.metadata = {k: v for k, v in self.metadata.items() if k != field}
                result = self.run_verification()
                self.assertEqual(result.identity_status, I.UNVERIFIED)
                self.assertEqual(result.availability_status, A.AVAILABLE)
                self.assertFalse(result.ready_for_market_data(as_of=self.now))

    def test_contradictions_are_not_hidden_by_missing_fields(self):
        self.ticker.metadata = {"symbol": "WRONG"}
        self.assertEqual(self.run_verification().identity_status, I.MISMATCH)

    def test_wrong_venue_currency_or_type_mismatches(self):
        for field, value in (("exchangeName", "FRA"), ("currency", "USD"),
                             ("instrumentType", "ETF"), ("symbol", "SAP")):
            with self.subTest(field=field):
                self.ticker.metadata = dict(self.metadata, **{field: value})
                result = self.run_verification()
                self.assertEqual(result.identity_status, I.MISMATCH)
                self.assertEqual(result.availability_status, A.AVAILABLE)

    def test_london_pence_preserved_with_explicit_scale(self):
        london = MarketListing("SHEL", "LSE", "LSE", "EUROPE", "GBP", "COMMON STOCK")
        self.ticker.metadata = {"symbol": "SHEL.L", "exchangeName": "LSE",
                                "currency": "GBp", "instrumentType": "EQUITY"}
        result = self.run_verification(london)
        self.assertEqual(result.identity_status, I.VERIFIED)
        evidence = dict(result.identity_evidence)
        self.assertEqual(evidence["yahoo.currency"], "GBp")
        self.assertEqual(evidence["price_unit_to_listing_currency"], "0.01")

    def test_known_isin_conflict_is_mismatch(self):
        listing = MarketListing("SAP", "XETRA", "XETRA", "EUROPE", "EUR",
                                "COMMON STOCK", isin="DE0007164600")
        self.ticker.metadata = dict(self.metadata, isin="WRONG")
        self.assertEqual(self.run_verification(listing).identity_status, I.MISMATCH)

    def test_empty_frame_is_no_data_not_not_found(self):
        self.ticker.frame = pd.DataFrame()
        self.ticker.metadata = AssertionError("metadata must not be fetched")
        result = self.run_verification()
        self.assertEqual(result.availability_status, A.NO_DATA)
        self.assertEqual(result.identity_status, I.UNVERIFIED)
        self.assertEqual(len(result.diagnostics), 1)

    def test_transient_errors_remain_retryable_and_do_not_leak_text(self):
        for exc in (TimeoutError("api_token=secret"), YFRateLimitError("private")):
            with self.subTest(exc=type(exc).__name__):
                self.ticker.frame = exc
                result = self.run_verification()
                self.assertEqual(result.availability_status, A.TEMPORARY_ERROR)
                self.assertNotIn("secret", repr(result))
                self.assertEqual(result.valid_bar_count, 0)

    def test_missing_prices_or_timezone_do_not_prove_nonexistence(self):
        for exc in (YFPricesMissingError("delisted?"), YFTzMissingError("delisted?")):
            self.ticker.frame = exc
            self.assertEqual(self.run_verification().availability_status, A.PROVIDER_ERROR)

    def test_http_404_alone_does_not_prove_symbol_nonexistence(self):
        class Response:
            status_code = 404
        exc = RuntimeError("not found")
        exc.response = Response()
        self.assertEqual(classify_exception(exc)[0], A.PROVIDER_ERROR)

    def test_metadata_failure_preserves_successful_prices(self):
        self.ticker.metadata = TimeoutError("private")
        result = self.run_verification()
        self.assertEqual(result.identity_status, I.UNVERIFIED)
        self.assertEqual(result.availability_status, A.AVAILABLE)
        self.assertEqual(result.diagnostics[0].code, "METADATA_NETWORK_ERROR")

    def test_invalid_bar_is_rejected_while_good_bar_survives(self):
        self.ticker.frame.loc[self.frame.index[0], "Close"] = float("inf")
        result = self.run_verification()
        self.assertEqual(result.valid_bar_count, 1)
        self.assertEqual(result.diagnostics[0].code, "BARS_REJECTED")

    def test_all_invalid_bars_are_no_data(self):
        self.ticker.frame["Close"] = -1
        self.assertEqual(self.run_verification().availability_status, A.NO_DATA)

    def test_bad_shape_and_naive_timestamps_are_provider_errors(self):
        for frame in (self.frame.drop(columns="Close"), self.frame.reset_index(), "bad"):
            self.ticker.frame = frame
            self.assertEqual(self.run_verification().availability_status, A.PROVIDER_ERROR)

    def test_out_of_window_bars_do_not_count(self):
        self.ticker.frame.index = self.frame.index - timedelta(days=100)
        self.assertEqual(self.run_verification().availability_status, A.NO_DATA)

    def test_unmapped_never_calls_yahoo(self):
        listing = MarketListing("TLV", "RO", "RO", "EUROPE", "RON", "COMMON STOCK")
        result = self.run_verification(listing)
        self.assertEqual(result.availability_status, A.NOT_CHECKED)
        self.assertEqual(self.ticker.calls, [])

    def test_wrong_mapping_and_future_window_rejected_before_io(self):
        with self.assertRaises(ValueError):
            self.verifier.verify(self.listing, YahooSymbolResolver().resolve(
                MarketListing("ABC", "NYSE", "NYSE", "US")), start=self.start, end=self.now)
        with self.assertRaises(ValueError):
            self.verifier.verify(self.listing, YahooSymbolResolver().resolve(self.listing),
                                  start=self.start, end=self.now + timedelta(days=1))
        self.assertEqual(self.ticker.calls, [])

    def test_unexpected_exception_is_provider_error(self):
        self.ticker.frame = RuntimeError("internal endpoint detail")
        self.assertEqual(self.run_verification().availability_status, A.PROVIDER_ERROR)


if __name__ == "__main__":
    unittest.main()
