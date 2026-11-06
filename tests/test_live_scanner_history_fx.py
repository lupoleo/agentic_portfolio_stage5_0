from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import pandas as pd
from app.scanner.ecb_session_fx import ECBSessionFXProvider
from app.scanner.exchange_session_calendar import ExchangeSessionCalendarProvider
from app.scanner.yahoo_history_snapshot import YahooHistorySnapshotProvider
from tools.live_scanner_history_quality import run_pilot

class FXPipelineTests(unittest.TestCase):
    def run_case(self, missing=False, failure=False):
        now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
        data = {"audit_id": "E2E-S2.2B", "decision_count": 1, "automatically_admitted_count": 1,
                "all_decisions": [{"symbol": "IBM", "exchange": "NYSE", "currency": "USD",
                                   "status": "ELIGIBLE", "raw_instrument_type": "COMMON STOCK"}]}
        def calendar(code, start, end):
            self.assertEqual(code, "XNYS")
            index = pd.bdate_range(start, end)
            closes = index.tz_localize("America/New_York") + pd.Timedelta(hours=16)
            return SimpleNamespace(schedule=pd.DataFrame({"close": closes}, index=index), tz="America/New_York")
        index = pd.bdate_range(end="2026-09-21", periods=70, tz="America/New_York")
        frame = pd.DataFrame({"Open": 100., "High": 110., "Low": 90., "Close": 100.,
                              "Adj Close": 95., "Volume": 20000.}, index=index)
        provider = YahooHistorySnapshotProvider(now=lambda: now, ticker_factory=lambda symbol: SimpleNamespace(
            history=lambda **kw: frame,
            get_history_metadata=lambda: {"symbol": "IBM", "exchangeName": "NYQ", "currency": "USD", "instrumentType": "EQUITY"}))
        dates = index[-20:-1] if missing else index[-20:]
        payload = ('<Envelope xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube>' +
                   ''.join(f'<Cube time="{d.date()}"><Cube currency="USD" rate="1.25"/></Cube>' for d in dates) +
                   '</Cube></Envelope>').encode()
        calls = []
        def opener(*args, **kw):
            calls.append(args)
            if failure:
                raise TimeoutError("secret")
            return BytesIO(payload)
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()):
            code = run_pilot(data, venues=["NYSE"], output=folder, now=lambda: now, pause_seconds=0,
                history_provider=provider, calendar_provider=ExchangeSessionCalendarProvider(factory=calendar, package_version="4.13.2"),
                fx_provider=ECBSessionFXProvider(opener=opener, now=lambda: now))
            report = json.loads(next(Path(folder).glob('*.json')).read_text())
        self.assertEqual(len(calls), 1)
        return code, report["results"][0]

    def test_usd_full_pipeline(self):
        code, record = self.run_case()
        self.assertEqual(code, 0)
        self.assertEqual(record["quality"]["route"], "STANDARD")
        self.assertEqual(dict(record["quality"]["metrics"])["median_turnover_eur"], 1600000.)
        self.assertEqual(len(record["fx"]["rates"]), 20)
        self.assertIn("raw_xml", record["fx"])

    def test_missing_day_is_review_not_low_liquidity(self):
        code, record = self.run_case(missing=True)
        self.assertEqual(code, 2)
        self.assertEqual(record["quality"]["route"], "REVIEW_REQUIRED")
        gate = next(g for g in record["quality"]["gates"] if g["name"] == "liquidity")
        self.assertEqual(gate["reason"], "TURNOVER_CONVERSION_UNDETERMINED")

    def test_fx_timeout_preserves_history(self):
        code, record = self.run_case(failure=True)
        self.assertEqual(code, 2)
        self.assertEqual(record["fx"]["status"], "ERROR")
        self.assertEqual(record["quality"]["route"], "REVIEW_REQUIRED")
        self.assertEqual(len(record["acquisition"]["snapshot"]["bars"]), 70)
        self.assertNotIn("secret", json.dumps(record))
