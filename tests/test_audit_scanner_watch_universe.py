from dataclasses import replace
from datetime import timedelta
import unittest

from app.scanner.watch_universe import watch_universe_to_dict
from tests.test_scanner_watch_universe import AS_OF, bundle, position
from tools.audit_scanner_watch_universe import (
    _aware,
    build_watch_universe_audit,
)


def reports(base=None):
    base = base or bundle()
    eligibility = {
        "audit_id": "E2E-S2.2B",
        "decision_count": 1,
        "all_decisions": [{
            "exchange": base.listing.exchange,
            "symbol": base.listing.symbol,
            "name": base.listing.name,
            "isin": base.listing.isin,
            "currency": base.listing.currency,
            "raw_instrument_type": base.eligibility.raw_instrument_type,
            "canonical_type": base.eligibility.canonical_type.value,
            "status": base.eligibility.status.value,
            "reason_code": base.eligibility.reason_code.value,
            "review_flags": [value.value for value in base.eligibility.review_flags],
            "policy_id": base.eligibility.policy_id,
            "policy_version": base.eligibility.policy_version,
        }],
    }
    mapping = {
        "audit_id": "E2E-S2.2C-MAPPING",
        "provider_id": base.mapping.provider_id,
        "mapping_version": base.mapping.mapping_version,
        "mapping_count": 1,
        "mappings": [{
            "exchange": base.listing.exchange,
            "symbol": base.listing.symbol,
            "mapping_status": base.mapping.status.value,
            "candidate_symbols": list(base.mapping.candidate_symbols),
            "rule_id": base.mapping.rule_id,
            "diagnostics": [watch_universe_to_dict(value) for value in base.mapping.diagnostics],
        }],
    }
    history = {
        "audit_id": "E2E-S2.2D-HISTORY-PILOT",
        "results": [{
            "quality": watch_universe_to_dict(base.history),
            "acquisition": {
                "verification": watch_universe_to_dict(base.verification),
            },
        }],
    }
    return eligibility, mapping, history


def audit(history_reports=None):
    eligibility, mapping, history = reports()
    return build_watch_universe_audit(
        eligibility,
        mapping,
        (history,) if history_reports is None else history_reports,
        (position(),),
        portfolio_snapshot_id="ACC-20260923",
        as_of=AS_OF,
        assembled_at=AS_OF + timedelta(seconds=1),
        source_fingerprints=(("fixture", "a" * 64),),
    )


class WatchUniverseAuditTests(unittest.TestCase):
    def test_cache_only_audit_assembles_candidate_position_and_union(self):
        result = audit()
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["summary"]["candidate_count"], 1)
        self.assertEqual(result["summary"]["portfolio_watch_count"], 1)
        self.assertEqual(result["summary"]["research_member_count"], 1)
        self.assertEqual(
            result["summary"]["provenance_counts"],
            {"CURRENT_POSITION+NEW_CANDIDATE": 1},
        )

    def test_missing_history_is_counted_not_silently_ignored(self):
        result = audit(history_reports=())
        self.assertEqual(result["summary"]["candidate_count"], 0)
        self.assertEqual(
            result["summary"]["candidate_reason_counts"],
            {"UPSTREAM_RESULT_MISSING": 1},
        )
        self.assertEqual(result["summary"]["portfolio_watch_count"], 1)

    def test_future_history_is_not_known_as_of(self):
        base = bundle()
        future = replace(base, history=replace(base.history, as_of=AS_OF + timedelta(seconds=1)))
        eligibility, mapping, history = reports(future)
        result = build_watch_universe_audit(
            eligibility, mapping, (history,), (),
            portfolio_snapshot_id="ACC-20260923", as_of=AS_OF,
            assembled_at=AS_OF + timedelta(seconds=2),
        )
        self.assertEqual(result["summary"]["candidate_count"], 0)

    def test_mapping_report_collision_is_rejected(self):
        eligibility, mapping, history = reports()
        mapping["mappings"].append(dict(mapping["mappings"][0]))
        mapping["mapping_count"] = 2
        with self.assertRaises(ValueError):
            build_watch_universe_audit(
                eligibility, mapping, (history,), (),
                portfolio_snapshot_id="ACC-20260923", as_of=AS_OF,
                assembled_at=AS_OF + timedelta(seconds=1),
            )


    def test_dotnet_round_trip_utc_timestamp_is_supported(
        self,
    ):
        parsed = _aware(
            "2026-09-23T17:30:51.3433260Z",
            "as_of",
        )

        self.assertEqual(
            parsed.isoformat(),
            "2026-09-23T17:30:51.343326+00:00",
        )

if __name__ == "__main__":
    unittest.main()
