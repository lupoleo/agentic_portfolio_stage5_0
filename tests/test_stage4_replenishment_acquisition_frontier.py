from datetime import datetime, timezone

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment import (
    build_candidate_frontier,
    candidate_frontier_evidence,
    create_replenishment_session,
    plan_candidate_wave,
)
from app.e2e.stage4_replenishment_contracts import (
    ReplenishmentDisposition,
    ReplenishmentStopReason,
)


NOW = datetime(2026, 9, 27, 15, 39, tzinfo=timezone.utc)


def reports():
    eligibility = {
        "all_decisions": [
            {"exchange": "BIT", "symbol": "A2A", "status": "ELIGIBLE"},
            {"exchange": "BIT", "symbol": "AMP", "status": "ELIGIBLE"},
            {"exchange": "BIT", "symbol": "AVIO", "status": "ELIGIBLE"},
            {"exchange": "XETRA", "symbol": "SAP", "status": "ELIGIBLE"},
            {"exchange": "NYSE", "symbol": "IBM", "status": "ELIGIBLE"},
            {"exchange": "BIT", "symbol": "BAD", "status": "INELIGIBLE"},
        ]
    }
    mapping = {
        "audit_id": "E2E-S2.2C-MAPPING",
        "mappings": [
            {
                "exchange": "BIT", "symbol": "A2A",
                "mapping_status": "RESOLVED",
                "yahoo_symbol": "A2A.MI",
            },
            {
                "exchange": "BIT", "symbol": "AMP",
                "mapping_status": "RESOLVED",
                "yahoo_symbol": "AMP.MI",
            },
            {
                "exchange": "BIT", "symbol": "AVIO",
                "mapping_status": "RESOLVED",
                "yahoo_symbol": "AVIO.MI",
            },
            {
                "exchange": "XETRA", "symbol": "SAP",
                "mapping_status": "RESOLVED",
                "yahoo_symbol": "SAP.DE",
            },
            {
                "exchange": "NYSE", "symbol": "IBM",
                "mapping_status": "UNMAPPED",
                "yahoo_symbol": None,
            },
        ],
    }
    return eligibility, mapping


def session():
    return create_replenishment_session(
        root_run_id="s4a-root",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=Stage4Mode.LIVE,
    )


def test_canonical_mapping_audit_field_builds_frontier():
    eligibility, mapping = reports()
    frontier = build_candidate_frontier(
        eligibility,
        mapping,
        attempted_listing_keys=("BIT:A2A", "XETRA:SAP"),
    )
    assert tuple(value.listing_key for value in frontier) == (
        "BIT:AMP",
        "BIT:AVIO",
    )
    assert tuple(value.yahoo_symbol for value in frontier) == (
        "AMP.MI",
        "AVIO.MI",
    )


def test_legacy_resolved_symbol_remains_compatible():
    eligibility = {
        "all_decisions": [
            {"exchange": "NASDAQ", "symbol": "MSFT", "status": "ELIGIBLE"},
        ]
    }
    mapping = {
        "mappings": [
            {
                "exchange": "NASDAQ", "symbol": "MSFT",
                "mapping_status": "RESOLVED",
                "resolved_symbol": "MSFT",
            }
        ]
    }
    frontier = build_candidate_frontier(eligibility, mapping)
    assert frontier[0].yahoo_symbol == "MSFT"


def test_frontier_evidence_proves_universe_is_not_exhausted():
    eligibility, mapping = reports()
    evidence = candidate_frontier_evidence(
        eligibility,
        mapping,
        attempted_listing_keys=("BIT:A2A", "XETRA:SAP"),
    )
    assert evidence == {
        "eligibility_input_count": 6,
        "mapping_input_count": 5,
        "supported_eligible_count": 5,
        "resolved_supported_eligible_count": 4,
        "unresolved_or_missing_supported_eligible_count": 1,
        "attempted_supported_eligible_count": 2,
        "acquirable_frontier_count": 2,
        "acquirable_frontier_sample": (
            "BIT:AMP",
            "BIT:AVIO",
        ),
    }


def test_attempted_a2a_and_sap_advance_to_acquisition_wave():
    eligibility, mapping = reports()
    plan = plan_candidate_wave(
        session=session(),
        wave_index=1,
        eligibility=eligibility,
        mapping=mapping,
        prior_outcomes=(),
        attempted_listing_keys=("BIT:A2A", "XETRA:SAP"),
    )
    assert plan.disposition is ReplenishmentDisposition.ADVANCE_FRONTIER
    assert plan.reason is ReplenishmentStopReason.NEXT_WAVE_READY
    assert tuple(
        value.listing_key for value in plan.selected_listings
    ) == ("BIT:AMP", "BIT:AVIO")


def test_conflicting_canonical_and_legacy_symbols_fail_closed():
    eligibility, mapping = reports()
    mapping["mappings"][0]["resolved_symbol"] = "WRONG.MI"

    try:
        build_candidate_frontier(eligibility, mapping)
    except ValueError as exc:
        assert "conflicting Yahoo symbols" in str(exc)
    else:
        raise AssertionError("mapping conflict was not rejected")
