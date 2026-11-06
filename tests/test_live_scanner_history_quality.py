from contextlib import redirect_stdout
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
import io
import json
import tempfile
from types import SimpleNamespace
import unittest

import pandas as pd

from app.scanner.exchange_session_calendar import ExchangeSessionCalendarProvider
from app.scanner.yahoo_history_snapshot import YahooHistorySnapshotProvider
from tools.live_scanner_history_quality import run_pilot


class YFRateLimitError(Exception):
    pass


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
        self.data = {"audit_id": "E2E-S2.2B", "decision_count": 2,
            "automatically_admitted_count": 2, "all_decisions": [
                {"symbol": "A2A", "exchange": "BIT", "currency": "EUR", "status": "ELIGIBLE", "raw_instrument_type": "COMMON_STOCK"},
                {"symbol": "SAP", "exchange": "XETRA", "currency": "EUR", "status": "ELIGIBLE", "raw_instrument_type": "COMMON STOCK"}]}
        self.calls = []
        self.failure = None
        def calendar_factory(code, start, end):
            index = pd.bdate_range(start, end)
            close = (index.tz_localize("Europe/Berlin") + pd.Timedelta(hours=17, minutes=30)).tz_convert("UTC")
            return SimpleNamespace(schedule=pd.DataFrame({"close": close}, index=index), tz="Europe/Berlin")
        self.calendar = ExchangeSessionCalendarProvider(factory=calendar_factory, package_version="4.13.2")
        def ticker_factory(symbol):
            def history(**kwargs):
                self.calls.append(symbol)
                if self.failure:
                    raise self.failure
                index = pd.bdate_range(end="2026-09-22", periods=70, tz="Europe/Berlin")
                return pd.DataFrame({"Open": 100., "High": 110., "Low": 90., "Close": 105.,
                    "Adj Close": 95., "Volume": 20_000.}, index=index)
            return SimpleNamespace(history=history, get_history_metadata=lambda: {
                "symbol": symbol, "exchangeName": "MIL" if symbol.endswith(".MI") else "GER",
                "currency": "EUR", "instrumentType": "EQUITY"})
        self.provider = YahooHistorySnapshotProvider(ticker_factory=ticker_factory, now=lambda: self.now)

    def run_case(self):
        with redirect_stdout(io.StringIO()):
            exit_code = run_pilot(self.data, venues=["BIT", "XETRA"], output=self.output,
                history_provider=self.provider, calendar_provider=self.calendar,
                now=lambda: self.now, pause_seconds=0)
        path = next(self.output.glob("scanner_history_quality_*.json"))
        return exit_code, json.loads(path.read_text())

    def test_complete_pipeline_persists_bars_verification_calendar_and_decision(self):
        exit_code, report = self.run_case()
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.calls, ["A2A.MI", "SAP.DE"])
        self.assertEqual(report["route_counts"], {"STANDARD": 2})
        for row in report["results"]:
            self.assertEqual(len(row["acquisition"]["snapshot"]["bars"]), 70)
            self.assertEqual(row["acquisition"]["verification"]["identity_status"], "VERIFIED")
            self.assertTrue(row["calendar"]["calendar"]["sessions"])
            self.assertEqual(dict(row["quality"]["metrics"])["excluded_pending_bars"], 1)
            self.assertIsNone(row["quality"]["listing_event"])

    def test_rate_limit_stops_before_second_listing(self):
        self.failure = YFRateLimitError("private-token")
        exit_code, report = self.run_case()
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["run_status"], "RATE_LIMIT_STOP")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(report["route_counts"], {"NOT_EVALUATED": 1})
        self.assertNotIn("private-token", json.dumps(report))

    def test_transport_error_not_reclassified_as_illiquid(self):
        self.failure = TimeoutError("secret")
        exit_code, report = self.run_case()
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["route_counts"], {"NOT_EVALUATED": 2})

    def test_non_eur_sample_rejected_before_any_fetch(self):
        self.data["all_decisions"][1]["currency"] = "USD"
        with self.assertRaises(ValueError):
            self.run_case()
        self.assertFalse(self.calls)

    def test_calendar_failure_is_checkpointed(self):
        self.calendar.package_version = "unsupported"
        exit_code, report = self.run_case()
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["run_status"], "FAILED")
        self.assertEqual(report["completed_count"], 0)
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
