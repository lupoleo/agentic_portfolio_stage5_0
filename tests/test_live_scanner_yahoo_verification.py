import json
from pathlib import Path
import tempfile
import unittest

from tools.live_scanner_yahoo_verification import select_sample, atomic_report


class LiveVerificationToolTests(unittest.TestCase):
    def setUp(self):
        self.data = {"audit_id": "E2E-S2.2B", "decision_count": 3,
                     "automatically_admitted_count": 2, "all_decisions": [
                         {"exchange": "NASDAQ", "symbol": "AAA", "status": "ELIGIBLE"},
                         {"exchange": "NASDAQ", "symbol": "MSFT", "status": "ELIGIBLE"},
                         {"exchange": "NASDAQ", "symbol": "BBB", "status": "REVIEW_REQUIRED"},
                     ]}

    def test_pilot_prefers_known_symbol_but_only_if_eligible(self):
        self.assertEqual(select_sample(self.data, ["NASDAQ"], 1)[0]["symbol"], "MSFT")
        self.data["all_decisions"][1]["status"] = "REVIEW_REQUIRED"
        self.data["automatically_admitted_count"] = 1
        self.assertEqual(select_sample(self.data, ["NASDAQ"], 1)[0]["symbol"], "AAA")

    def test_missing_duplicate_or_unsupported_venues_rejected(self):
        for venues in (["NYSE"], ["NASDAQ", "NASDAQ"], ["RO"], []):
            with self.subTest(venues=venues), self.assertRaises(ValueError):
                select_sample(self.data, venues, 1)

    def test_collisions_are_checked_before_sampling(self):
        self.data["all_decisions"].append({"exchange": "NYSE", "symbol": "MSFT", "status": "ELIGIBLE"})
        self.data["decision_count"] += 1
        self.data["automatically_admitted_count"] += 1
        self.assertEqual(select_sample(self.data, ["NASDAQ"], 1)[0]["symbol"], "AAA")

    def test_checkpoint_replacement_is_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            atomic_report(path, {"results": []})
            atomic_report(path, {"results": [{"status": "AVAILABLE"}]})
            self.assertEqual(json.loads(path.read_text())["results"][0]["status"], "AVAILABLE")
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
