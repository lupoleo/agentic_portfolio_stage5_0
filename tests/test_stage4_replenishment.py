from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment import (
    build_candidate_frontier,
    create_replenishment_session,
    plan_candidate_wave,
)
from app.e2e.stage4_replenishment_contracts import (
    CandidateReplenishmentPolicy,
    ReplenishmentDisposition,
    ReplenishmentStopReason,
)


NOW = datetime(2026, 9, 27, 14, 13, 22, tzinfo=timezone.utc)


def reports():
    eligibility = {"all_decisions": [
        {"exchange": "XETRA", "symbol": "SAP", "status": "ELIGIBLE"},
        {"exchange": "BIT", "symbol": "A2A", "status": "ELIGIBLE"},
        {"exchange": "NYSE", "symbol": "IBM", "status": "ELIGIBLE"},
        {"exchange": "NASDAQ", "symbol": "MSFT", "status": "ELIGIBLE"},
        {"exchange": "LSE", "symbol": "III", "status": "ELIGIBLE"},
        {"exchange": "BIT", "symbol": "BAD", "status": "INELIGIBLE"},
    ]}
    mapping = {"mappings": [
        {"exchange": "XETRA", "symbol": "SAP", "mapping_status": "RESOLVED", "resolved_symbol": "SAP.DE"},
        {"exchange": "BIT", "symbol": "A2A", "mapping_status": "RESOLVED", "resolved_symbol": "A2A.MI"},
        {"exchange": "NYSE", "symbol": "IBM", "mapping_status": "RESOLVED", "resolved_symbol": "IBM"},
        {"exchange": "NASDAQ", "symbol": "MSFT", "mapping_status": "RESOLVED", "resolved_symbol": "MSFT"},
        {"exchange": "LSE", "symbol": "III", "mapping_status": "RESOLVED", "resolved_symbol": "III.L"},
        {"exchange": "BIT", "symbol": "BAD", "mapping_status": "UNSUPPORTED", "resolved_symbol": None},
    ]}
    return eligibility, mapping


def session(policy=None):
    return create_replenishment_session(
        root_run_id="s4a-root",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=Stage4Mode.LIVE,
        policy=policy,
    )


def outcome(kind, status="EXCLUDED", reason="RESEARCH_NOT_COMPLETE", opportunity_id=None):
    return SimpleNamespace(
        kind=kind,
        status=status,
        reason=reason,
        opportunity_id=opportunity_id,
    )


def test_policy_is_fail_closed():
    with pytest.raises(ValueError, match="threshold"):
        CandidateReplenishmentPolicy(allow_threshold_relaxation=True)
    with pytest.raises(ValueError, match="execution"):
        CandidateReplenishmentPolicy(allow_execution=True)


def test_frontier_is_canonical_and_excludes_attempted():
    eligibility, mapping = reports()
    frontier = build_candidate_frontier(
        eligibility,
        mapping,
        attempted_listing_keys=("BIT:A2A", "XETRA:SAP"),
    )
    assert tuple(value.listing_key for value in frontier) == (
        "NASDAQ:MSFT",
        "NYSE:IBM",
    )


def test_terminal_exclusions_advance_two_listing_frontier():
    eligibility, mapping = reports()
    value = plan_candidate_wave(
        session=session(),
        wave_index=2,
        eligibility=eligibility,
        mapping=mapping,
        prior_outcomes=(
            outcome("NEW_LONG"),
            outcome("NEW_SHORT"),
        ),
        attempted_listing_keys=("BIT:A2A", "XETRA:SAP"),
    )
    assert value.disposition is ReplenishmentDisposition.ADVANCE_FRONTIER
    assert value.reason is ReplenishmentStopReason.NEXT_WAVE_READY
    assert tuple(item.listing_key for item in value.selected_listings) == (
        "NASDAQ:MSFT",
        "NYSE:IBM",
    )


def test_opportunity_stops_replenishment():
    eligibility, mapping = reports()
    value = plan_candidate_wave(
        session=session(), wave_index=2,
        eligibility=eligibility, mapping=mapping,
        prior_outcomes=(outcome(
            "NEW_LONG", status="OPPORTUNITY_CREATED",
            reason="OPPORTUNITY_CREATED", opportunity_id="opp-1",
        ),),
        attempted_listing_keys=("BIT:A2A",),
    )
    assert value.disposition is ReplenishmentDisposition.STOP_FAIL_CLOSED
    assert value.reason is ReplenishmentStopReason.SELECTABLE_OPPORTUNITY_FOUND


def test_pending_directional_work_does_not_advance():
    eligibility, mapping = reports()
    value = plan_candidate_wave(
        session=session(), wave_index=2,
        eligibility=eligibility, mapping=mapping,
        prior_outcomes=(outcome(
            "NEW_LONG", status="PENDING", reason="CACHE_ONLY_MISS",
        ),),
        attempted_listing_keys=("BIT:A2A",),
    )
    assert value.reason is ReplenishmentStopReason.PENDING_DIRECTIONAL_WORK


def test_transient_failure_retries_once():
    eligibility, mapping = reports()
    value = plan_candidate_wave(
        session=session(), wave_index=1,
        eligibility=eligibility, mapping=mapping,
        prior_outcomes=(outcome(
            "NEW_LONG", status="FAILED", reason="PROCESSING_FAILED",
        ),),
        attempted_listing_keys=("BIT:A2A",),
        current_listing_keys=("BIT:A2A",),
        retry_count=0,
    )
    assert value.disposition is ReplenishmentDisposition.RETRY_CURRENT
    assert value.retry_count == 1


def test_transient_failure_is_quarantined_after_retry_budget():
    eligibility, mapping = reports()
    value = plan_candidate_wave(
        session=session(),
        wave_index=2,
        eligibility=eligibility,
        mapping=mapping,
        prior_outcomes=(outcome(
            "NEW_LONG",
            status="FAILED",
            reason="PROCESSING_FAILED",
        ),),
        attempted_listing_keys=("BIT:A2A",),
        current_listing_keys=("BIT:A2A",),
        retry_count=1,
    )

    assert value.disposition is (
        ReplenishmentDisposition.ADVANCE_FRONTIER
    )
    assert value.reason is (
        ReplenishmentStopReason.NEXT_WAVE_READY
    )
    assert value.wave_index == 2
    assert value.retry_count == 0
    assert tuple(
        item.listing_key
        for item in value.selected_listings
    ) == (
        "NASDAQ:MSFT",
        "NYSE:IBM",
    )
    assert (
        "transient_retry_exhausted_quarantine"
        in value.diagnostics
    )
    assert (
        "quarantined_listing_keys=BIT:A2A"
        in value.diagnostics
    )


def test_wave_budget_is_bounded():
    eligibility, mapping = reports()
    policy = CandidateReplenishmentPolicy(max_waves=1)
    value = plan_candidate_wave(
        session=session(policy), wave_index=2,
        eligibility=eligibility, mapping=mapping,
        prior_outcomes=(outcome("NEW_LONG"),),
        attempted_listing_keys=("BIT:A2A",),
    )
    assert value.reason is ReplenishmentStopReason.REPLENISHMENT_BUDGET_EXHAUSTED
