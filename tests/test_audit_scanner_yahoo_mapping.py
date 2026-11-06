from copy import deepcopy
import unittest

from tools.audit_scanner_yahoo_mapping import build_mapping_audit


class MappingAuditTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "audit_id": "E2E-S2.2B", "decision_count": 4,
            "automatically_admitted_count": 2,
            "all_decisions": [
                {"exchange": "BIT", "symbol": "A2A", "status": "ELIGIBLE"},
                {"exchange": "RO", "symbol": "TLV", "status": "ELIGIBLE"},
                {"exchange": "NYSE", "symbol": "W", "status": "REVIEW_REQUIRED"},
                {"exchange": "NYSE", "symbol": "E", "status": "INELIGIBLE"},
            ],
        }

    def test_only_eligible_inputs_are_mapped_and_source_is_unchanged(self):
        original = deepcopy(self.data)
        report = build_mapping_audit(self.data)
        self.assertEqual(self.data, original)
        self.assertEqual(report["network_calls"], 0)
        self.assertEqual(report["mapping_count"], 2)
        self.assertEqual(report["status_counts"], {"RESOLVED": 1, "UNMAPPED": 1})

    def test_bad_counts_and_statuses_rejected(self):
        for field, value in (("decision_count", 3), ("automatically_admitted_count", 3),
                             ("audit_id", "OTHER")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_mapping_audit(dict(self.data, **{field: value}))
        self.data["all_decisions"][0]["status"] = "UNKNOWN"
        with self.assertRaises(ValueError):
            build_mapping_audit(self.data)

    def test_duplicates_rejected(self):
        self.data["all_decisions"][1] = self.data["all_decisions"][0]
        with self.assertRaises(ValueError):
            build_mapping_audit(self.data)
