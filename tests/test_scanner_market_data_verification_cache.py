from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import io
from contextlib import redirect_stdout
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.scanner.market_data_contracts import (
    MarketDataVerification, MarketDataIdentityStatus as I,
    MarketDataAvailabilityStatus as A, MarketDataDiagnostic as D,
)
from app.scanner.market_data_verification_cache import CachedMarketDataVerifier
from app.scanner.yahoo_market_data_verifier import VERIFICATION_VERSION
from app.scanner.yahoo_symbol_resolver import YahooSymbolResolver
from app.scanner.universe_models import MarketListing
from tools.live_scanner_yahoo_verification import run_context


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        self.end = self.now
        self.start = self.end - timedelta(days=30)
        self.listing = MarketListing("SAP", "XETRA", "XETRA", "EUROPE", "EUR", "COMMON STOCK")
        self.mapping = YahooSymbolResolver().resolve(self.listing)
        self.calls = 0
        self.status = A.AVAILABLE

    def verify(self, listing, mapping, *, start, end):
        self.calls += 1
        available = self.status == A.AVAILABLE
        return MarketDataVerification(mapping, I.VERIFIED if available else I.UNVERIFIED,
            self.status, self.now, self.now + timedelta(days=1), start, end,
            VERIFICATION_VERSION, valid_bar_count=1 if available else 0,
            latest_bar_at=end - timedelta(days=1) if available else None,
            identity_evidence=(("symbol", mapping.resolved_symbol),) if available else (),
            diagnostics=() if available else (D("RATE_LIMIT" if self.status == A.TEMPORARY_ERROR else "NO_DATA", "Unavailable"),))

    def wrapper(self, mode="prefer-cache", **kwargs):
        return CachedMarketDataVerifier(self, self.root, mode=mode, now=lambda: self.now, **kwargs)

    def request(self, wrapper=None, **kwargs):
        args = dict(listing=self.listing, mapping=self.mapping, start=self.start, end=self.end)
        args.update(kwargs)
        return (wrapper or self.wrapper()).verify(**args)

    def test_fresh_hit_survives_new_wrapper_and_preserves_evidence(self):
        first = self.request()
        second = self.request(self.wrapper("cache-only"))
        self.assertEqual(first, replace(second, from_cache=False))
        self.assertTrue(second.from_cache)
        self.assertEqual(self.calls, 1)

    def test_cache_only_miss_has_no_io_to_provider(self):
        result = self.request(self.wrapper("cache-only"))
        self.assertEqual(result.availability_status, A.NOT_CHECKED)
        self.assertEqual(result.diagnostics[0].code, "CACHE_MISS")
        self.assertFalse(result.ready_for_market_data(as_of=self.now))
        self.assertEqual(self.calls, 0)

    def test_expiry_boundary_never_uses_stale(self):
        self.request()
        self.now += timedelta(days=1)
        result = self.request(self.wrapper("cache-only"))
        self.assertEqual(result.diagnostics[0].code, "CACHE_EXPIRED")
        self.request()
        self.assertEqual(self.calls, 2)

    def test_force_refresh_ignores_fresh_cache(self):
        self.request()
        self.assertFalse(self.request(self.wrapper("force-refresh")).from_cache)
        self.assertEqual(self.calls, 2)

    def test_source_mapping_and_window_changes_miss(self):
        self.request()
        for change in ({"listing": replace(self.listing, currency="USD")},
                       {"listing": replace(self.listing, isin="DE0007164600")},
                       {"mapping": replace(self.mapping, mapping_version="v2")},
                       {"mapping": replace(self.mapping, candidate_symbols=("SAP2.DE",))},
                       {"start": self.start - timedelta(days=1)},
                       {"end": self.end - timedelta(hours=1)}):
            with self.subTest(change=change):
                self.assertEqual(self.request(self.wrapper("cache-only"), **change).availability_status, A.NOT_CHECKED)
        self.assertEqual(self.calls, 1)

    def test_verification_version_change_misses(self):
        self.request()
        self.assertEqual(self.request(self.wrapper("cache-only", verification_version="v2")).availability_status, A.NOT_CHECKED)

    def test_corrupt_json_is_reported_and_recoverable(self):
        self.request()
        next(self.root.glob("*.json")).write_text("broken")
        self.assertEqual(self.request(self.wrapper("cache-only")).diagnostics[0].code, "CACHE_INVALID")
        self.request()
        self.assertTrue(self.request().from_cache)

    def test_mismatching_result_is_not_reused(self):
        self.request()
        path = next(self.root.glob("*.json"))
        raw = json.loads(path.read_text())
        raw["result"]["verification_version"] = "other"
        path.write_text(json.dumps(raw))
        self.assertEqual(self.request(self.wrapper("cache-only")).diagnostics[0].code, "CACHE_INVALID")

    def test_future_dated_cache_is_not_reused(self):
        self.end -= timedelta(hours=1)
        self.request()
        self.now -= timedelta(seconds=1)
        result = self.request(self.wrapper("cache-only"))
        self.assertEqual(result.availability_status, A.NOT_CHECKED)
        self.assertEqual(result.diagnostics[0].code, "CACHE_EXPIRED")

    def test_transient_cooldown_is_five_minutes_then_retry(self):
        self.status = A.TEMPORARY_ERROR
        first = self.request()
        self.assertEqual(first.expires_at - first.checked_at, timedelta(minutes=5))
        self.assertTrue(self.request().from_cache)
        self.now += timedelta(minutes=5)
        self.request()
        self.assertEqual(self.calls, 2)

    def test_no_data_expires_after_one_hour(self):
        self.status = A.NO_DATA
        result = self.request()
        self.assertEqual(result.expires_at - result.checked_at, timedelta(hours=1))

    def test_write_failure_preserves_result_with_diagnostic(self):
        with patch("app.scanner.market_data_verification_cache.atomic_json", side_effect=OSError):
            result = self.request()
        self.assertTrue(result.ready_for_market_data(as_of=self.now))
        self.assertEqual(result.diagnostics[-1].code, "CACHE_WRITE_FAILED")

    def test_partial_run_reuses_completed_then_fetches_missing(self):
        self.request()
        resumed = self.wrapper()
        self.request(resumed)
        other = replace(self.listing, symbol="BMW")
        self.request(resumed, listing=other, mapping=YahooSymbolResolver().resolve(other))
        self.assertEqual((resumed.cache_hits, resumed.network_verifications), (1, 1))

    def test_resume_freezes_window_and_checks_source_and_selection(self):
        data, selected = {"audit": "source"}, [{"symbol": "SAP"}]
        sig, start, end = run_context(data, selected, 30)
        report = {"audit_id": "E2E-S2.2C-VERIFICATION-PILOT", "resume_signature": sig,
                  "lookback_days": 30, "requested_start": start.isoformat(), "requested_end": end.isoformat()}
        self.assertEqual(run_context(data, selected, 30, report), (sig, start, end))
        for args in (({}, selected, 30), (data, [], 30), (data, selected, 20)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                run_context(*args, resume=report)

    def test_old_pilot_report_cannot_claim_resume_support(self):
        with self.assertRaises(ValueError):
            run_context({}, [], 30, {"audit_id": "E2E-S2.2C-VERIFICATION-PILOT"})

    def test_cli_resume_cache_only_performs_no_provider_verifications(self):
        from tools.live_scanner_yahoo_verification import main
        self.now = datetime.now(timezone.utc)
        source = self.root / "input.json"
        source.write_text(json.dumps({"audit_id": "E2E-S2.2B", "decision_count": 1,
            "automatically_admitted_count": 1, "all_decisions": [{
                "exchange": "XETRA", "symbol": "SAP", "status": "ELIGIBLE",
                "currency": "EUR", "raw_instrument_type": "COMMON STOCK"}]}))
        args = ["pilot", "--input-report", str(source), "--venues", "XETRA",
                "--cache-directory", str(self.root / "cache"),
                "--output-directory", str(self.root / "reports"), "--pause-seconds", "0"]
        with patch.dict("sys.modules", {"yfinance": SimpleNamespace(__version__="fake")}), \
                patch("tools.live_scanner_yahoo_verification.YahooMarketDataVerifier", return_value=self), \
                redirect_stdout(io.StringIO()):
            with patch("sys.argv", args):
                self.assertEqual(main(), 0)
            report = next((self.root / "reports").glob("*.json"))
            with patch("sys.argv", args + ["--mode", "cache-only", "--resume-report", str(report)]):
                self.assertEqual(main(), 0)
        latest = sorted((self.root / "reports").glob("*.json"))[-1]
        payload = json.loads(latest.read_text())
        self.assertEqual(payload["cache_hits"], 1)
        self.assertEqual(payload["network_verifications"], 0)
        self.assertEqual(self.calls, 1)
