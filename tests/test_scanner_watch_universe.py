from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from app.portfolio.models import PortfolioPosition
from app.scanner.history_quality_contracts import (
    GateStatus,
    HistoryGate,
    HistoryQualityPolicy,
    HistoryQualityResult,
    HistoryRoute,
    IndicatorCapability,
)
from app.scanner.instrument_eligibility import (
    InstrumentEligibilityStatus,
    evaluate_instrument_eligibility,
)
from app.scanner.market_data_contracts import (
    MarketDataAvailabilityStatus,
    MarketDataIdentityStatus,
    MarketDataVerification,
)
from app.scanner.universe_models import MarketListing
from app.scanner.watch_universe import (
    assemble_research_watch_universe,
    watch_universe_to_dict,
)
from app.scanner.watch_universe_contracts import (
    CandidateAssemblyStatus,
    CandidateEvidenceBundle,
    CandidateExclusionReason,
    PortfolioWatchReason,
    ResearchReadiness,
    WatchProvenance,
)
from app.scanner.yahoo_symbol_resolver import YahooSymbolResolver


UTC = timezone.utc
AS_OF = datetime(2026, 9, 23, 18, tzinfo=UTC)


def listing(symbol="SAP", exchange="XETRA", isin="DE0007164600"):
    return MarketListing(
        symbol, exchange, exchange, "EUROPE", currency="EUR",
        instrument_type="COMMON STOCK", isin=isin, name=f"{symbol} SE",
    )


def bundle(*, item=None, route=HistoryRoute.STANDARD, liquidity=GateStatus.PASS,
           liquidity_reason="LIQUIDITY_OK", recent=False):
    item = item or listing()
    eligibility = evaluate_instrument_eligibility(item)
    mapping = YahooSymbolResolver().resolve(item)
    verification = MarketDataVerification(
        mapping=mapping,
        identity_status=MarketDataIdentityStatus.VERIFIED,
        availability_status=MarketDataAvailabilityStatus.AVAILABLE,
        checked_at=AS_OF - timedelta(hours=1),
        expires_at=AS_OF + timedelta(hours=23),
        requested_start=AS_OF - timedelta(days=30),
        requested_end=AS_OF,
        verification_version="test-verification-v1",
        valid_bar_count=20,
        latest_bar_at=AS_OF - timedelta(days=1),
        identity_evidence=(("yahoo.currency", "EUR"),),
    )
    gates = [HistoryGate("market_data", GateStatus.PASS, "VERIFICATION_READY")]
    if route is HistoryRoute.RECENT_LISTING:
        gates.extend((
            HistoryGate("technical_inputs", GateStatus.UNDETERMINED, "PARTIAL_INDICATOR_INPUTS"),
            HistoryGate("history_maturity", GateStatus.UNDETERMINED, "VERIFIED_RECENT_LISTING"),
        ))
    gates.append(HistoryGate("liquidity", liquidity, liquidity_reason))
    indicators = (
        IndicatorCapability("SMA20", route is HistoryRoute.STANDARD, 20,
                            20 if route is HistoryRoute.STANDARD else 5,
                            "SUFFICIENT_ALIGNED_SESSIONS" if route is HistoryRoute.STANDARD
                            else "INSUFFICIENT_ALIGNED_SESSIONS"),
    )
    history = HistoryQualityResult(
        listing_key=item.key,
        yahoo_symbol=mapping.resolved_symbol,
        as_of=AS_OF - timedelta(minutes=1),
        policy=HistoryQualityPolicy(),
        snapshot_fingerprint="snapshot-fingerprint",
        evidence_fingerprint="evidence-fingerprint",
        evidence_sources=(("history", "fixture"),),
        route=route,
        gates=tuple(gates),
        indicators=indicators,
        metrics=(("recent_listing_verified", recent),),
        listing_event="IPO" if recent else None,
    )
    return CandidateEvidenceBundle(item, eligibility, mapping, verification, history)


def position(*, symbol="SAP.FRA", isin="DE0007164600", quantity=10.,
             instrument_type="AZIONE", market="XETRA"):
    return PortfolioPosition(
        name="SAP SE", isin=isin, broker_symbol=symbol, market=market,
        instrument_type=instrument_type, currency="EUR", quantity=quantity,
        average_price=100., load_exchange_rate=1., cost_value_eur=1000.,
        market_price=110., market_exchange_rate=1., market_value_eur=1100.,
        pnl_percent=10., pnl_eur=100., pnl_currency=100.,
    )


def assemble(*, bundles=(), positions=()):
    return assemble_research_watch_universe(
        candidate_inputs=bundles,
        portfolio_positions=positions,
        portfolio_snapshot_id="ACC-20260923",
        as_of=AS_OF,
        assembled_at=AS_OF + timedelta(seconds=1),
        source_fingerprints=(("eligibility", "a" * 64),),
    )


class WatchUniverseTests(unittest.TestCase):
    def test_standard_candidate_is_included(self):
        result = assemble(bundles=(bundle(),))
        self.assertEqual(len(result.candidate_set), 1)
        self.assertEqual(result.candidate_decisions[0].status,
                         CandidateAssemblyStatus.INCLUDED)
        self.assertEqual(result.candidate_decisions[0].reason,
                         CandidateExclusionReason.INCLUDED)

    def test_ineligible_and_review_required_are_explicitly_excluded(self):
        base = bundle()
        for status, reason in (
            (InstrumentEligibilityStatus.INELIGIBLE,
             CandidateExclusionReason.ELIGIBILITY_NOT_ELIGIBLE),
            (InstrumentEligibilityStatus.REVIEW_REQUIRED,
             CandidateExclusionReason.ELIGIBILITY_REVIEW_REQUIRED),
        ):
            with self.subTest(status=status):
                changed = replace(
                    base,
                    eligibility=replace(base.eligibility, status=status),
                )
                result = assemble(bundles=(changed,))
                self.assertEqual(result.candidate_decisions[0].reason, reason)
                self.assertFalse(result.candidate_set)

    def test_missing_mapping_verification_and_history_fail_closed(self):
        base = bundle()
        cases = (
            replace(base, mapping=None, verification=None, history=None),
            replace(base, verification=None, history=None),
            replace(base, history=None),
        )
        for changed in cases:
            with self.subTest(changed=changed):
                result = assemble(bundles=(changed,))
                self.assertEqual(
                    result.candidate_decisions[0].reason,
                    CandidateExclusionReason.UPSTREAM_RESULT_MISSING,
                )

    def test_blocked_and_review_history_are_excluded(self):
        for route, reason in (
            (HistoryRoute.BLOCKED, CandidateExclusionReason.HISTORY_BLOCKED),
            (HistoryRoute.REVIEW_REQUIRED,
             CandidateExclusionReason.HISTORY_REVIEW_REQUIRED),
        ):
            base = bundle()
            changed = replace(base, history=replace(base.history, route=route))
            with self.subTest(route=route):
                self.assertEqual(
                    assemble(bundles=(changed,)).candidate_decisions[0].reason,
                    reason,
                )

    def test_verified_recent_listing_accepts_only_short_history_exception(self):
        accepted = bundle(
            route=HistoryRoute.RECENT_LISTING,
            liquidity=GateStatus.UNDETERMINED,
            liquidity_reason="INSUFFICIENT_ALIGNED_VOLUME_HISTORY",
            recent=True,
        )
        self.assertEqual(len(assemble(bundles=(accepted,)).candidate_set), 1)

        for change in (
            {"listing_event": None},
            {"metrics": (("recent_listing_verified", False),)},
            {"gates": accepted.history.gates + (
                HistoryGate("freshness", GateStatus.UNDETERMINED, "NO_REFERENCE"),
            )},
            {"gates": tuple(
                replace(gate, reason="TURNOVER_CONVERSION_UNDETERMINED")
                if gate.name == "liquidity" else gate
                for gate in accepted.history.gates
            )},
        ):
            rejected = replace(accepted, history=replace(accepted.history, **change))
            with self.subTest(change=change):
                decision = assemble(bundles=(rejected,)).candidate_decisions[0]
                self.assertEqual(
                    decision.reason,
                    CandidateExclusionReason.RECENT_LISTING_POLICY_NOT_MET,
                )

    def test_current_long_and_short_positions_are_always_watched(self):
        result = assemble(positions=(
            position(quantity=10.),
            position(symbol="MUCFD.CFD", isin="", quantity=-25., market="USA"),
        ))
        self.assertEqual(len(result.portfolio_watch_set), 2)
        self.assertEqual(
            {item.direction for item in result.portfolio_watch_set},
            {"LONG", "SHORT"},
        )
        self.assertTrue(all(
            decision.reason is PortfolioWatchReason.CURRENT_POSITION
            for decision in result.portfolio_decisions
        ))

    def test_unresolved_current_position_is_preserved_as_degraded(self):
        result = assemble(positions=(
            position(symbol="UNKNOWN.CODE", isin="", instrument_type="CERT", market="OTC"),
        ))
        self.assertEqual(len(result.portfolio_watch_set), 1)
        self.assertIsNone(result.portfolio_watch_set[0].yahoo_symbol)
        self.assertEqual(result.members[0].readiness, ResearchReadiness.DEGRADED)
        self.assertIn("MARKET_DATA_SYMBOL_UNRESOLVED", result.members[0].diagnostics)

    def test_flat_position_is_explicitly_excluded(self):
        result = assemble(positions=(position(quantity=0.),))
        self.assertFalse(result.portfolio_watch_set)
        self.assertEqual(result.portfolio_decisions[0].reason,
                         PortfolioWatchReason.FLAT_POSITION)

    def test_candidate_and_position_with_same_yahoo_factor_are_one_member(self):
        result = assemble(
            bundles=(bundle(),),
            positions=(position(),),
        )
        self.assertEqual(len(result.members), 1)
        self.assertEqual(
            set(result.members[0].provenances),
            {WatchProvenance.NEW_CANDIDATE, WatchProvenance.CURRENT_POSITION},
        )
        self.assertEqual(len(result.members[0].candidate_keys), 1)
        self.assertEqual(len(result.members[0].position_refs), 1)

    def test_same_yahoo_with_conflicting_isin_requires_review(self):
        ibm = bundle(item=listing("IBM", "NYSE", "US4592001014"))
        result = assemble(
            bundles=(ibm,),
            positions=(position(symbol="IBM.N", isin="DIFFERENT-ISIN", market="NYSE"),),
        )
        self.assertEqual(len(result.members), 1)
        self.assertEqual(result.members[0].readiness,
                         ResearchReadiness.REVIEW_REQUIRED)
        self.assertIn("IDENTITY_CONFLICT", result.members[0].diagnostics)

    def test_duplicate_listing_and_mixed_identity_are_contract_errors(self):
        base = bundle()
        with self.assertRaises(ValueError):
            assemble(bundles=(base, base))
        other = bundle(item=listing("IBM", "NYSE", "US4592001014"))
        with self.assertRaises(ValueError):
            CandidateEvidenceBundle(
                base.listing, other.eligibility, base.mapping,
                base.verification, base.history,
            )

    def test_ordering_serialization_and_fingerprint_are_deterministic(self):
        a = bundle()
        b = bundle(item=listing("IBM", "NYSE", "US4592001014"))
        first = assemble(
            bundles=(a, b),
            positions=(position(), position(symbol="MUCFD.CFD", isin="", quantity=-5.)),
        )
        second = assemble(
            bundles=(b, a),
            positions=(position(symbol="MUCFD.CFD", isin="", quantity=-5.), position()),
        )
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.run_id, second.run_id)
        payload = watch_universe_to_dict(first)
        self.assertEqual(payload, watch_universe_to_dict(second))
        json.dumps(payload, allow_nan=False)

        replay = assemble_research_watch_universe(
            candidate_inputs=(b, a),
            portfolio_positions=(position(), position(symbol="MUCFD.CFD", isin="", quantity=-5.)),
            portfolio_snapshot_id="ACC-20260923",
            as_of=AS_OF,
            assembled_at=AS_OF + timedelta(minutes=10),
            source_fingerprints=(("eligibility", "a" * 64),),
        )
        self.assertEqual(first.fingerprint, replay.fingerprint)
        self.assertNotEqual(first.assembled_at, replay.assembled_at)

    def test_json_helper_orders_unordered_collections(self):
        self.assertEqual(
            watch_universe_to_dict({"values": {"b", "a"}}),
            {"values": ["a", "b"]},
        )


if __name__ == "__main__":
    unittest.main()
