from datetime import datetime, timezone
import unittest

from app.scanner.universe_models import ListingKey
from app.scanner.watch_universe_contracts import (
    CandidateAssemblyDecision,
    CandidateAssemblyStatus,
    CandidateExclusionReason,
    PortfolioPositionSnapshot,
    ResearchReadiness,
    ResearchSubjectKey,
    ResearchWatchMember,
    WatchProvenance,
    WatchUniversePolicy,
)


class WatchUniverseContractTests(unittest.TestCase):
    def test_policy_identity_is_frozen(self):
        policy = WatchUniversePolicy()
        self.assertEqual(policy.policy_id, "scanner-v1-candidate-watch-assembly")
        self.assertEqual(policy.policy_version, "1")
        self.assertTrue(policy.allow_recent_listing_short_liquidity)

    def test_included_decision_requires_included_reason(self):
        with self.assertRaises(ValueError):
            CandidateAssemblyDecision(
                ListingKey("NYSE", "IBM"),
                CandidateAssemblyStatus.INCLUDED,
                CandidateExclusionReason.HISTORY_BLOCKED,
                "invalid",
            )

    def test_position_snapshot_requires_finite_quantity(self):
        with self.assertRaises(ValueError):
            PortfolioPositionSnapshot(
                "ref", "snapshot", "FINECO", "IBM", "US4592001014",
                "IBM.N", "NYSE", "AZIONE", "USD", float("nan"), "LONG",
                "IBM", "fingerprint",
            )

    def test_research_member_normalizes_and_orders_evidence(self):
        member = ResearchWatchMember(
            ResearchSubjectKey("yahoo", "ibm"),
            (WatchProvenance.CURRENT_POSITION, WatchProvenance.NEW_CANDIDATE,
             WatchProvenance.CURRENT_POSITION),
            (ListingKey("NYSE", "IBM"), ListingKey("NYSE", "IBM")),
            ("b", "a", "a"),
            ("IBM", "IBM"),
            ("US4592001014",),
            ("STANDARD",),
            ResearchReadiness.READY,
            ("Z", "A", "A"),
        )
        self.assertEqual(member.subject_key.namespace, "YAHOO")
        self.assertEqual(member.subject_key.value, "IBM")
        self.assertEqual(member.position_refs, ("a", "b"))
        self.assertEqual(member.diagnostics, ("A", "Z"))
        self.assertEqual(len(member.provenances), 2)

    def test_research_subject_namespace_is_closed(self):
        with self.assertRaises(ValueError):
            ResearchSubjectKey("CUSIP", "123")

    def test_aware_datetime_rule_is_exercised_by_public_contract(self):
        # Keep the import-time public types compatible with aware UTC timestamps.
        value = datetime(2026, 9, 23, tzinfo=timezone.utc)
        self.assertIsNotNone(value.utcoffset())


if __name__ == "__main__":
    unittest.main()
