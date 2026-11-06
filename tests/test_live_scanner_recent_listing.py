from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import pandas as pd

from app.scanner.exchange_session_calendar import ExchangeSessionCalendarProvider
from app.scanner.yahoo_history_snapshot import YahooHistorySnapshotProvider
from app.scanner.listing_start_reference import (
    ListingStartReferenceRegistry,
    SCHEMA,
)
from tools.live_scanner_history_quality import run_pilot


class RecentListingPipelineTests(unittest.TestCase):
    def run_case(
        self,
        count=5,
        event="IPO",
        reference=True,
        invalid_price=False,
        volume=20000.,
        extra_before=False,
        conflict=False,
        future_review=False,
        eligible=True,
        missing_latest=False,
    ):
        now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)

        index = pd.bdate_range(
            end="2026-09-21",
            periods=count,
            tz="Europe/Berlin",
        )

        reviewed_at = (
            now + timedelta(seconds=1)
            if future_review
            else now
        )

        document = {
            "schema": SCHEMA,
            "references": [
                {
                    "exchange": "XETRA",
                    "symbol": "NEW",
                    "issuer_name": "Synthetic Example",
                    "isin": "OTHER" if conflict else None,
                    "first_session": index[0].date().isoformat(),
                    "event_kind": event,
                    "ipo_confirmed": event == "IPO",
                    "date_status": "CONFIRMED_TRADING_START",
                    "source_kind": "ISSUER",
                    "source_url": "https://example.org/synthetic-only",
                    "source_title": "Synthetic reference",
                    "source_excerpt": (
                        "Synthetic confirmed start of trading"
                    ),
                    "observed_at": now.isoformat(),
                    "reviewed_at": reviewed_at.isoformat(),
                    "reviewed_by": "test-fixture",
                    "review_status": "APPROVED",
                }
            ],
        }

        if extra_before:
            index = pd.bdate_range(
                end="2026-09-21",
                periods=count + 1,
                tz="Europe/Berlin",
            )

        if missing_latest:
            index = index[:-1]

        frame = pd.DataFrame(
            {
                "Open": 100.,
                "High": 110.,
                "Low": 90.,
                "Close": 100.,
                "Adj Close": 95.,
                "Volume": volume,
            },
            index=index,
        )

        if invalid_price:
            frame.iloc[0, frame.columns.get_loc("High")] = 50.

        def calendar(code, start, end):
            dates = pd.bdate_range(start, end)
            closes = (
                dates.tz_localize("Europe/Berlin")
                + pd.Timedelta(hours=17, minutes=30)
            )
            return SimpleNamespace(
                schedule=pd.DataFrame({"close": closes}, index=dates),
                tz="Europe/Berlin",
            )

        calls = []

        def ticker(symbol):
            calls.append(symbol)
            return SimpleNamespace(
                history=lambda **kwargs: frame,
                get_history_metadata=lambda: {
                    "symbol": symbol,
                    "exchangeName": "GER",
                    "currency": "EUR",
                    "instrumentType": "EQUITY",
                },
            )

        data = {
            "audit_id": "E2E-S2.2B",
            "decision_count": 2,
            "automatically_admitted_count": 2 if eligible else 1,
            "all_decisions": [
                {
                    "symbol": "SAP",
                    "exchange": "XETRA",
                    "currency": "EUR",
                    "status": "ELIGIBLE",
                    "raw_instrument_type": "COMMON STOCK",
                },
                {
                    "symbol": "NEW",
                    "exchange": "XETRA",
                    "currency": "EUR",
                    "status": "ELIGIBLE" if eligible else "INELIGIBLE",
                    "raw_instrument_type": "COMMON STOCK",
                    "isin": "ID",
                },
            ],
        }

        registry = (
            ListingStartReferenceRegistry(document)
            if reference
            else None
        )

        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()):
            code = run_pilot(
                data,
                venues=["XETRA"],
                output=folder,
                now=lambda: now,
                pause_seconds=0,
                history_provider=YahooHistorySnapshotProvider(
                    ticker_factory=ticker,
                    now=lambda: now,
                ),
                calendar_provider=ExchangeSessionCalendarProvider(
                    factory=calendar,
                    package_version="4.13.2",
                ),
                listing_references=registry,
                listing_keys=["XETRA:NEW"],
            )

            report = json.loads(
                next(Path(folder).glob("*.json")).read_text()
            )

        self.assertEqual(calls, ["NEW.DE"])
        return code, report["results"][0]

    def test_five_session_ipo_preserved_with_partial_capabilities(self):
        code, row = self.run_case()

        self.assertEqual(code, 2)
        self.assertEqual(row["quality"]["route"], "RECENT_LISTING")
        self.assertEqual(row["quality"]["listing_event"], "IPO")
        self.assertFalse(
            any(c["available"] for c in row["quality"]["indicators"])
        )
        self.assertEqual(row["listing_reference"]["status"], "MATCHED")
        self.assertIn(
            "source_excerpt",
            row["listing_reference"]["reference"],
        )

    def test_non_ipo_listing_stays_distinct(self):
        _, row = self.run_case(event="LISTING_START")

        self.assertEqual(row["quality"]["route"], "RECENT_LISTING")
        self.assertEqual(
            row["quality"]["listing_event"],
            "LISTING_START",
        )

    def test_partial_and_mature_indicator_capabilities(self):
        for count in (15, 21, 50):
            with self.subTest(count=count):
                code, row = self.run_case(count=count)

                capabilities = {
                    c["name"]: c["available"]
                    for c in row["quality"]["indicators"]
                }

                self.assertTrue(capabilities["RSI14"])
                self.assertEqual(capabilities["SMA50"], count == 50)
                self.assertEqual(
                    row["quality"]["route"],
                    "STANDARD" if count == 50 else "RECENT_LISTING",
                )
                self.assertEqual(code, 0 if count == 50 else 2)

    def test_short_history_without_reference_never_claims_ipo(self):
        _, row = self.run_case(reference=False)

        self.assertNotEqual(
            row["quality"]["route"],
            "RECENT_LISTING",
        )
        self.assertIsNone(row["quality"]["listing_event"])

    def test_real_failures_not_waived_by_recent_listing(self):
        changes_to_test = (
            {"invalid_price": True},
            {"extra_before": True},
            {"missing_latest": True},
            {"count": 21, "volume": 100.},
        )

        for changes in changes_to_test:
            with self.subTest(changes=changes):
                _, row = self.run_case(**changes)
                self.assertEqual(row["quality"]["route"], "BLOCKED")

    def test_conflicting_or_future_reference_never_silently_discarded(self):
        for changes in ({"conflict": True}, {"future_review": True}):
            with self.subTest(changes=changes):
                code, row = self.run_case(**changes)

                self.assertEqual(code, 2)
                self.assertIsNone(row["quality"])
                self.assertIsNone(
                    row["listing_reference"]["evidence"]
                )

    def test_explicit_selection_cannot_bypass_eligibility(self):
        with self.assertRaises(ValueError):
            self.run_case(eligible=False)
