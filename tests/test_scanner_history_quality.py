from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import unittest
import json

from app.scanner.universe_models import ListingKey
from app.scanner.yahoo_symbol_resolver import YahooSymbolResolver
from app.scanner.market_data_contracts import (
    MarketDataVerification, MarketDataAvailabilityStatus as A,
    MarketDataIdentityStatus as I, MarketDataDiagnostic,
)
from app.scanner.history_quality_contracts import (
    DailyHistoryBar, HistorySnapshot, SessionCalendar, MarketSession,
    ListingStartEvidence, SessionFXRate, HistoryQualityPolicy, HistoryRoute as R,
    GateStatus as S,
)
from app.scanner.history_quality import evaluate_history_quality, history_quality_result_to_dict


UTC = timezone.utc


class HistoryQualityTests(unittest.TestCase):
    def inputs(self, count=70, *, recent=False, unit="EUR"):
        first = date(2026, 4, 1)
        days = []
        while len(days) < count:
            if first.weekday() < 5:
                days.append(first)
            first += timedelta(days=1)
        sessions = tuple(MarketSession(d, datetime.combine(d, time(16), UTC)) for d in days)
        as_of = sessions[-1].closes_at + timedelta(hours=2)
        key = ListingKey("XETRA", "SAP")
        mapping = YahooSymbolResolver().resolve(key)
        bars = tuple(DailyHistoryBar(d, 100., 110., 90., 100., 20_000., 95.) for d in days)
        snapshot = HistorySnapshot(key, "SAP.DE", mapping.mapping_version, as_of,
            days[0], days[-1], unit, "synthetic-fixture", "explicit-adjusted-close", bars)
        verification = MarketDataVerification(mapping, I.VERIFIED, A.AVAILABLE,
            as_of - timedelta(minutes=1), as_of + timedelta(hours=23),
            sessions[0].closes_at - timedelta(days=1), as_of, "scanner-yahoo-verification-v1",
            valid_bar_count=count, latest_bar_at=sessions[-1].closes_at,
            identity_evidence=(("yahoo.currency", unit),))
        return dict(snapshot=snapshot, verification=verification, as_of=as_of,
            calendar=SessionCalendar("XETRA", "synthetic-calendar", "1", days[0], days[-1], sessions),
            listing_start=ListingStartEvidence(key, days[0], "synthetic-reference", as_of, "IPO") if recent else None)

    def gate(self, result, name):
        return next(g for g in result.gates if g.name == name)

    def test_standard_aligned_history(self):
        result = evaluate_history_quality(**self.inputs())
        self.assertEqual(result.route, R.STANDARD)
        self.assertTrue(result.standard_ready)
        self.assertEqual(dict(result.metrics)["median_turnover_eur"], 2_000_000.)
        self.assertTrue(all(c.available for c in result.indicators))

    def test_ipo_with_five_sessions_is_preserved(self):
        result = evaluate_history_quality(**self.inputs(5, recent=True))
        self.assertEqual(result.route, R.RECENT_LISTING)
        self.assertEqual(result.listing_event, "IPO")
        self.assertEqual(self.gate(result, "liquidity").status, S.UNDETERMINED)
        self.assertFalse(result.standard_ready)
        self.assertTrue(all(not c.available for c in result.indicators))

    def test_short_history_does_not_infer_ipo(self):
        result = evaluate_history_quality(**self.inputs(20))
        self.assertEqual(result.route, R.REVIEW_REQUIRED)
        self.assertIsNone(result.listing_event)

    def test_recent_listing_is_not_automatically_ipo(self):
        data = self.inputs(30, recent=True)
        data["listing_start"] = replace(data["listing_start"], event_kind="LISTING_START")
        result = evaluate_history_quality(**data)
        self.assertEqual(result.route, R.RECENT_LISTING)
        self.assertEqual(result.listing_event, "LISTING_START")

    def test_50_session_ipo_can_enter_standard(self):
        result = evaluate_history_quality(**self.inputs(50, recent=True))
        self.assertEqual(result.route, R.STANDARD)
        self.assertEqual(result.listing_event, "IPO")
        self.assertEqual(dict(result.metrics)["annual_window_observed_sessions"], 50)

    def test_indicator_boundaries(self):
        for count, available in ((14, set()), (15, {"RSI14"}),
                                 (20, {"RSI14", "SMA20"}),
                                 (21, {"RSI14", "SMA20", "VOLATILITY20", "RVOL20"}),
                                 (50, {"RSI14", "SMA20", "VOLATILITY20", "RVOL20", "SMA50", "TREND"})):
            with self.subTest(count=count):
                result = evaluate_history_quality(**self.inputs(count, recent=True))
                self.assertEqual({c.name for c in result.indicators if c.available}, available)

    def test_missing_calendar_is_undetermined(self):
        data = self.inputs()
        data["calendar"] = None
        result = evaluate_history_quality(**data)
        self.assertEqual(result.route, R.REVIEW_REQUIRED)
        self.assertEqual(self.gate(result, "freshness").status, S.UNDETERMINED)

    def test_incomplete_calendar_is_not_guessed(self):
        data = self.inputs()
        data["calendar"] = replace(data["calendar"], coverage_start=data["calendar"].coverage_start + timedelta(days=1),
                                   sessions=data["calendar"].sessions[1:])
        self.assertEqual(evaluate_history_quality(**data).route, R.REVIEW_REQUIRED)

    def test_holiday_does_not_count_as_missing_session(self):
        data = self.inputs()
        day = data["calendar"].sessions[-5].session
        data["calendar"] = replace(data["calendar"], sessions=tuple(s for s in data["calendar"].sessions if s.session != day))
        data["snapshot"] = replace(data["snapshot"], bars=tuple(b for b in data["snapshot"].bars if b.session != day))
        self.assertEqual(evaluate_history_quality(**data).route, R.STANDARD)

    def test_weekend_freshness_uses_friday(self):
        data = self.inputs()
        # Artificial schedule ends on Friday for this case.
        while data["calendar"].sessions[-1].session.weekday() != 4:
            data = self.inputs(len(data["calendar"].sessions) + 1)
        as_of = data["as_of"] + timedelta(days=1)
        data["as_of"] = as_of
        data["verification"] = replace(data["verification"], expires_at=as_of + timedelta(hours=1))
        data["calendar"] = replace(data["calendar"], coverage_end=as_of.date())
        self.assertEqual(evaluate_history_quality(**data).route, R.STANDARD)

    def test_pending_session_excluded_until_close_plus_grace(self):
        data = self.inputs()
        data["as_of"] = data["calendar"].sessions[-1].closes_at + timedelta(minutes=30)
        data["snapshot"] = replace(data["snapshot"], fetched_at=data["as_of"])
        data["verification"] = replace(data["verification"], checked_at=data["as_of"] - timedelta(minutes=1))
        result = evaluate_history_quality(**data)
        self.assertEqual(result.route, R.STANDARD)
        self.assertEqual(dict(result.metrics)["excluded_pending_bars"], 1)

    def test_missing_latest_session_blocks(self):
        data = self.inputs()
        data["snapshot"] = replace(data["snapshot"], bars=data["snapshot"].bars[:-1])
        result = evaluate_history_quality(**data)
        self.assertEqual(result.route, R.BLOCKED)
        self.assertEqual(self.gate(result, "freshness").status, S.FAIL)

    def test_missing_interior_bar_does_not_compress_indicator_window(self):
        data = self.inputs()
        data["snapshot"] = replace(data["snapshot"], bars=data["snapshot"].bars[:-10] + data["snapshot"].bars[-9:])
        result = evaluate_history_quality(**data)
        self.assertEqual(result.route, R.REVIEW_REQUIRED)
        self.assertEqual(self.gate(result, "coverage").status, S.PASS)
        self.assertFalse(next(c for c in result.indicators if c.name == "SMA20").available)

    def test_coverage_threshold_boundary(self):
        for removed, status in ((3, S.PASS), (4, S.FAIL)):
            data = self.inputs(100)
            bars = data["snapshot"].bars
            data["snapshot"] = replace(data["snapshot"], bars=bars[:41] + bars[41 + removed:])
            with self.subTest(removed=removed):
                self.assertEqual(self.gate(evaluate_history_quality(**data), "coverage").status, status)

    def test_invalid_ohlc_blocks_and_is_counted(self):
        for change in ({"close": float("nan")}, {"close": -1.}, {"high": 50.}, {"open": float("inf")}):
            data = self.inputs()
            bars = data["snapshot"].bars
            data["snapshot"] = replace(data["snapshot"], bars=(replace(bars[0], **change),) + bars[1:])
            with self.subTest(change=change):
                result = evaluate_history_quality(**data)
                self.assertEqual(result.route, R.BLOCKED)
                self.assertEqual(dict(result.metrics)["invalid_price_bars"], 1)

    def test_duplicate_bar_blocks(self):
        data = self.inputs()
        data["snapshot"] = replace(data["snapshot"], bars=data["snapshot"].bars + (data["snapshot"].bars[0],))
        self.assertEqual(evaluate_history_quality(**data).route, R.BLOCKED)

    def test_missing_adjusted_close_prevents_standard(self):
        data = self.inputs()
        bars = data["snapshot"].bars
        data["snapshot"] = replace(data["snapshot"], bars=bars[:-1] + (replace(bars[-1], adjusted_close=None),))
        result = evaluate_history_quality(**data)
        self.assertEqual(result.route, R.REVIEW_REQUIRED)
        self.assertEqual(self.gate(result, "liquidity").status, S.PASS)

    def test_missing_volume_is_not_zero(self):
        data = self.inputs()
        bars = data["snapshot"].bars
        data["snapshot"] = replace(data["snapshot"], bars=bars[:-1] + (replace(bars[-1], volume=None),))
        result = evaluate_history_quality(**data)
        self.assertEqual(self.gate(result, "liquidity").status, S.UNDETERMINED)
        self.assertIsNone(dict(result.metrics)["median_turnover_eur"])

    def test_negative_infinite_and_boolean_volume_block(self):
        for volume in (-1., float("inf"), True):
            data = self.inputs()
            bars = data["snapshot"].bars
            data["snapshot"] = replace(data["snapshot"], bars=bars[:-1] + (replace(bars[-1], volume=volume),))
            with self.subTest(volume=volume):
                self.assertEqual(evaluate_history_quality(**data).route, R.BLOCKED)

    def test_audit_serialization_preserves_sources_and_has_no_nan(self):
        result = evaluate_history_quality(**self.inputs(5, recent=True))
        payload = json.loads(json.dumps(history_quality_result_to_dict(result), allow_nan=False))
        self.assertEqual(payload["route"], "RECENT_LISTING")
        self.assertEqual(payload["policy"]["publication_grace"], 3600.)
        self.assertEqual(dict(payload["evidence_sources"])["history"], "synthetic-fixture")

    def test_zero_volume_and_low_turnover_are_failures(self):
        for volume in (0., 100.):
            data = self.inputs()
            data["snapshot"] = replace(data["snapshot"], bars=tuple(replace(b, volume=volume) for b in data["snapshot"].bars))
            result = evaluate_history_quality(**data)
            self.assertEqual(result.route, R.BLOCKED)
            self.assertEqual(self.gate(result, "liquidity").status, S.FAIL)

    def test_liquidity_exact_threshold_passes(self):
        data = self.inputs()
        data["snapshot"] = replace(data["snapshot"], bars=tuple(replace(b, volume=10_000.) for b in data["snapshot"].bars))
        self.assertEqual(evaluate_history_quality(**data).route, R.STANDARD)

    def test_fx_missing_and_future_evidence_remain_unknown(self):
        data = self.inputs(unit="USD")
        self.assertEqual(self.gate(evaluate_history_quality(**data), "liquidity").status, S.UNDETERMINED)
        data["fx_rates"] = tuple(SessionFXRate(b.session, "USD", .9, "fixture", data["as_of"] + timedelta(seconds=1))
                                 for b in data["snapshot"].bars)
        self.assertEqual(self.gate(evaluate_history_quality(**data), "liquidity").status, S.UNDETERMINED)
        data["fx_rates"] = tuple(replace(r, known_at=data["as_of"]) for r in data["fx_rates"])
        self.assertEqual(evaluate_history_quality(**data).route, R.STANDARD)

    def test_pence_scale_is_applied_before_fx(self):
        data = self.inputs(unit="GBp")
        data["fx_rates"] = tuple(SessionFXRate(b.session, "GBP", 1.2, "fixture", data["as_of"])
                                 for b in data["snapshot"].bars)
        result = evaluate_history_quality(**data)
        self.assertEqual(dict(result.metrics)["median_turnover_eur"], 24_000.)
        self.assertEqual(result.route, R.BLOCKED)

    def test_price_unit_conflict_blocks(self):
        data = self.inputs()
        data["snapshot"] = replace(data["snapshot"], price_unit="USD")
        self.assertEqual(evaluate_history_quality(**data).route, R.BLOCKED)

    def test_future_snapshot_and_listing_evidence_not_accepted(self):
        for field in ("snapshot", "listing_start"):
            data = self.inputs(20, recent=True)
            data[field] = replace(data[field], **{"fetched_at" if field == "snapshot" else "known_at": data["as_of"] + timedelta(seconds=1)})
            with self.subTest(field=field):
                self.assertEqual(evaluate_history_quality(**data).route, R.REVIEW_REQUIRED)

    def test_expired_verification_cannot_admit(self):
        data = self.inputs()
        data["verification"] = replace(data["verification"], expires_at=data["as_of"])
        self.assertEqual(evaluate_history_quality(**data).route, R.REVIEW_REQUIRED)

    def test_identity_mismatch_blocks(self):
        data = self.inputs()
        data["verification"] = replace(data["verification"], identity_status=I.MISMATCH,
            diagnostics=(MarketDataDiagnostic("IDENTITY_MISMATCH", "Contradiction"),))
        self.assertEqual(evaluate_history_quality(**data).route, R.BLOCKED)

    def test_mixed_listing_calendar_and_mapping_rejected(self):
        for field in ("snapshot", "calendar", "listing_start"):
            data = self.inputs(20, recent=True)
            data[field] = replace(data[field], **({"exchange": "NYSE"} if field == "calendar"
                                                   else {"listing_key": ListingKey("NYSE", "IBM")}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                evaluate_history_quality(**data)

    def test_fingerprints_are_repeatable_and_bind_data_and_policy(self):
        data = self.inputs()
        a = evaluate_history_quality(**data)
        self.assertEqual(a, evaluate_history_quality(**data))
        b = evaluate_history_quality(**data, policy=HistoryQualityPolicy(minimum_median_turnover_eur=3_000_000.))
        self.assertNotEqual(a.evidence_fingerprint, b.evidence_fingerprint)
        self.assertEqual(a.snapshot_fingerprint, b.snapshot_fingerprint)
        self.assertEqual(b.route, R.BLOCKED)

    def test_invalid_policy_and_calendar_contracts(self):
        for change in ({"min_standard_sessions": 49}, {"minimum_coverage": 1.1},
                       {"minimum_median_turnover_eur": float("nan")}, {"liquidity_window": True},
                       {"publication_grace": timedelta(seconds=-1)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                HistoryQualityPolicy(**change)
        data = self.inputs()
        with self.assertRaises(ValueError):
            replace(data["calendar"], sessions=data["calendar"].sessions * 2)


if __name__ == "__main__":
    unittest.main()
