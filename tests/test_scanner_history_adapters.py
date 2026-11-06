from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
import unittest
from types import SimpleNamespace

import pandas as pd

from app.scanner.exchange_session_calendar import ExchangeSessionCalendarProvider
from app.scanner.yahoo_history_snapshot import YahooHistorySnapshotProvider, frame_to_snapshot
from app.scanner.yahoo_symbol_resolver import YahooSymbolResolver
from app.scanner.universe_models import MarketListing
from app.scanner.market_data_contracts import MarketDataAvailabilityStatus as A, MarketDataIdentityStatus as I


class CalendarAdapterTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.schedule = pd.DataFrame({"close": pd.to_datetime([
            "2026-09-11T15:30:00Z", "2026-09-14T15:30:00Z",
            "2026-09-15T12:00:00Z", "2026-09-17T15:30:00Z",
            "2026-09-18T15:30:00Z", "2026-09-25T15:30:00Z"])},
            index=pd.to_datetime(["2026-09-11", "2026-09-14", "2026-09-15", "2026-09-17", "2026-09-18", "2026-09-25"]))
        def factory(code, **kwargs):
            self.calls.append((code, kwargs))
            return SimpleNamespace(schedule=self.schedule, tz="Europe/Berlin")
        self.provider = ExchangeSessionCalendarProvider(factory=factory, package_version="4.13.2")

    def build(self, venue="XETRA"):
        return self.provider.build(venue, start=date(2026, 9, 14), end=date(2026, 9, 20))

    def test_explicit_binding_preserves_holiday_and_early_close(self):
        result = self.build()
        self.assertEqual(self.calls[0][0], "XETR")
        self.assertEqual(result.calendar.version, "4.13.2")
        self.assertEqual(result.calendar.exchange, "XETRA")
        self.assertEqual(len(result.calendar.sessions), 4)
        self.assertEqual(result.calendar.sessions[1].closes_at.hour, 12)
        self.assertNotIn(date(2026, 9, 16), [s.session for s in result.calendar.sessions])

    def test_milan_and_nyse_have_own_codes(self):
        for venue, code in (("BIT", "XMIL"), ("NYSE", "XNYS")):
            with self.subTest(venue=venue):
                self.assertEqual(self.build(venue).calendar_code, code)

    def test_nasdaq_binding_uses_library_alias_and_preserves_listing_venue(self):
        result = self.build("NASDAQ")
        self.assertEqual(self.calls[0][0], "NASDAQ")
        self.assertEqual(result.calendar_code, "NASDAQ")
        self.assertEqual(result.calendar.exchange, "NASDAQ")

    def test_no_unknown_venue_fallback(self):
        with self.assertRaises(ValueError):
            self.build("UNKNOWN")
        self.assertFalse(self.calls)

    def test_wrong_version_fails_before_factory(self):
        self.provider.package_version = "4.12"
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.calls)

    def test_naive_close_and_duplicate_schedule_rejected(self):
        original = self.schedule
        for schedule in (original.assign(close=original["close"].dt.tz_localize(None)),
                         pd.concat([original, original.iloc[:1]])):
            self.schedule = schedule
            with self.subTest(), self.assertRaises(ValueError):
                self.build()

    def test_truncated_schedule_cannot_claim_full_coverage(self):
        self.schedule = self.schedule.iloc[2:4]
        with self.assertRaises(ValueError):
            self.build()


class HistoryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
        self.start, self.end = date(2026, 9, 14), date(2026, 9, 22)
        self.listing = MarketListing("SAP", "XETRA", "XETRA", "EUROPE", "EUR", "COMMON STOCK")
        self.mapping = YahooSymbolResolver().resolve(self.listing)
        self.frame = pd.DataFrame({"Open": [100.] * 6, "High": [110.] * 6,
            "Low": [90.] * 6, "Close": [105.] * 6, "Adj Close": [95.] * 6, "Volume": [20_000.] * 6},
            index=pd.bdate_range("2026-09-14", periods=6, tz="Europe/Berlin"))
        self.metadata = {"symbol": "SAP.DE", "exchangeName": "GER", "currency": "EUR", "instrumentType": "EQUITY"}
        self.calls, self.metadata_calls = [], 0
        self.provider = YahooHistorySnapshotProvider(ticker_factory=lambda symbol: self, now=lambda: self.now)

    def history(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.frame, Exception):
            raise self.frame
        return self.frame

    def get_history_metadata(self):
        self.metadata_calls += 1
        if isinstance(self.metadata, Exception):
            raise self.metadata
        return self.metadata

    def fetch(self, **kwargs):
        options = dict(start=self.start, end=self.end, timezone="Europe/Berlin")
        options.update(kwargs)
        return self.provider.fetch(self.listing, self.mapping, **options)

    def test_single_frame_for_verification_and_nominal_adjusted_snapshot(self):
        result = self.fetch()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.metadata_calls, 1)
        self.assertEqual(result.verification.identity_status, I.VERIFIED)
        self.assertEqual(result.snapshot.bars[0].close, 105.)
        self.assertEqual(result.snapshot.bars[0].adjusted_close, 95.)
        self.assertEqual(result.snapshot.bars[0].session, self.start)
        self.assertTrue(self.calls[0]["keepna"])
        for key in ("auto_adjust", "back_adjust", "repair", "actions"):
            self.assertFalse(self.calls[0][key])
        self.assertEqual(self.calls[0]["end"], self.now)

    def test_local_dates_not_utc_midnight_dates(self):
        self.frame.index = self.frame.index.tz_convert("UTC")
        self.assertEqual(self.frame.index[0].date(), date(2026, 9, 13))
        self.assertEqual(self.fetch().snapshot.bars[0].session, date(2026, 9, 14))

    def test_preserves_missing_rows_and_does_not_mutate_frame(self):
        self.frame.loc[self.frame.index[1], "Close"] = float("nan")
        original = self.frame.copy(deep=True)
        result = self.fetch()
        self.assertEqual(len(result.snapshot.bars), 6)
        self.assertIsNone(result.snapshot.bars[1].close)
        pd.testing.assert_frame_equal(original, self.frame)

    def test_missing_adjusted_close_is_not_replaced_with_close(self):
        self.frame = self.frame.drop(columns="Adj Close")
        result = self.fetch()
        self.assertTrue(all(b.adjusted_close is None for b in result.snapshot.bars))
        self.assertEqual(result.diagnostics[0].code, "ADJUSTED_CLOSE_MISSING")

    def test_zero_volume_retains_explicit_source_ambiguity(self):
        self.frame.loc[self.frame.index[0], "Volume"] = 0
        result = self.fetch()
        self.assertIsNone(result.snapshot.bars[0].volume)
        self.assertEqual(result.ambiguous_zero_volume_sessions, (self.start,))
        self.assertEqual(result.diagnostics[0].code, "ZERO_VOLUME_AMBIGUOUS")

    def test_empty_provider_session_is_explicit_and_preserved(self):
        session = self.frame.index[1]
        self.frame.loc[
            session,
            ["Open", "High", "Low", "Close", "Adj Close"],
        ] = float("nan")
        self.frame.loc[session, "Volume"] = 0

        result = self.fetch()

        bar = result.snapshot.bars[1]
        self.assertIsNone(bar.open)
        self.assertIsNone(bar.high)
        self.assertIsNone(bar.low)
        self.assertIsNone(bar.close)
        self.assertIsNone(bar.adjusted_close)
        self.assertIsNone(bar.volume)

        diagnostic_codes = {
            diagnostic.code
            for diagnostic in result.diagnostics
        }
        self.assertIn(
            "EMPTY_PROVIDER_SESSION",
            diagnostic_codes,
        )
        self.assertIn(
            "ZERO_VOLUME_AMBIGUOUS",
            diagnostic_codes,
        )

    def test_negative_volume_reaches_quality_gate(self):
        self.frame.loc[self.frame.index[0], "Volume"] = -1
        self.assertEqual(self.fetch().snapshot.bars[0].volume, -1.)

    def test_timeout_has_no_synthetic_empty_snapshot(self):
        self.frame = TimeoutError("private token")
        result = self.fetch()
        self.assertIsNone(result.snapshot)
        self.assertEqual(result.verification.availability_status, A.TEMPORARY_ERROR)
        self.assertEqual(self.metadata_calls, 0)
        self.assertNotIn("private", repr(result))

    def test_metadata_failure_retains_price_status_without_snapshot(self):
        self.metadata = RuntimeError("secret")
        result = self.fetch()
        self.assertEqual(result.verification.availability_status, A.AVAILABLE)
        self.assertEqual(result.verification.identity_status, I.UNVERIFIED)
        self.assertIsNone(result.snapshot)

    def test_empty_frame_never_guesses_ipo(self):
        self.frame = pd.DataFrame()
        result = self.fetch()
        self.assertEqual(result.verification.availability_status, A.NO_DATA)
        self.assertIsNone(result.snapshot)

    def test_bad_timestamps_fail_snapshot_construction(self):
        original = self.frame.copy()
        for index in (original.index.tz_localize(None), original.index + pd.Timedelta(hours=1)):
            self.frame = original.copy()
            self.frame.index = index
            with self.subTest(index=index):
                self.assertIsNone(self.fetch().snapshot)

    def test_duplicate_and_out_of_range_rows_are_not_silently_dropped(self):
        original = self.frame.copy()
        self.frame = pd.concat([original, original.iloc[:1]])
        self.assertIsNone(self.fetch().snapshot)
        self.frame = original
        self.assertIsNone(self.fetch(start=date(2026, 9, 15)).snapshot)

    def test_future_request_rejected_without_provider_call(self):
        with self.assertRaises(ValueError):
            self.fetch(end=self.end + timedelta(days=1))
        self.assertFalse(self.calls)

    def test_identity_conflict_retained_for_downstream_block(self):
        self.metadata["symbol"] = "OTHER.DE"
        result = self.fetch()
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(result.verification.identity_status, I.MISMATCH)


if __name__ == "__main__":
    unittest.main()
