from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.cio.cio_decision_engine import (
    CioDecisionEngine,
)
from app.cio.models import (
    CioDecisionType,
    Currency,
    Direction,
    ExecutionSide,
    PortfolioRiskDelta,
    PortfolioRiskState,
    PortfolioSimulation,
    RequiredChangeType,
    TradeProposal,
)


NOW = datetime(
    2026,
    8,
    21,
    12,
    0,
    tzinfo=timezone.utc,
)


# =============================================================
# Fixtures
# =============================================================


def _proposal(
    *,
    proposal_id: str = "PROP-SMCI-DECISION",
    opportunity_id: str = "OPP-SMCI-DECISION",
    snapshot_id: str = "SNAP-SMCI-DECISION",
    estimated_max_loss_eur: float | None = None,
) -> TradeProposal:

    return TradeProposal(
        proposal_id=proposal_id,
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        created_at=NOW,

        ticker="SMCI",
        direction=Direction.SHORT,

        instrument_id=(
            "FIN-SMCI-ORDINARY-ORDINARY-X1"
        ),

        sizing_id="SIZ-SMCI-DECISION",

        execution_side=(
            ExecutionSide.SELL_SHORT
        ),

        quantity=155,
        reference_price=37.45,
        currency=Currency.USD,

        entry_type="MARKET",

        entry_price=None,
        stop_price=None,
        target_1=None,
        target_2=None,

        gross_exposure_eur=4_992.09,

        estimated_margin_eur=None,

        estimated_max_loss_eur=(
            estimated_max_loss_eur
        ),

        expected_holding_min_days=5,
        expected_holding_max_days=15,
    )


def _before() -> PortfolioRiskState:

    return PortfolioRiskState(
        gross_exposure_eur=371_343.34,
        net_exposure_eur=371_343.34,

        long_exposure_eur=371_343.34,
        short_exposure_eur=0.0,

        portfolio_volatility_pct=1.09,
        portfolio_beta=1.067,

        var_95_1d_eur=11_572.57,
        cvar_95_1d_eur=16_225.16,

        top5_concentration_pct=40.86,
        effective_positions=19.33,

        analytical_coverage_pct=95.45,
    )


def _after() -> PortfolioRiskState:

    return PortfolioRiskState(
        gross_exposure_eur=376_335.43,
        net_exposure_eur=366_351.25,

        long_exposure_eur=371_343.34,
        short_exposure_eur=4_992.09,

        portfolio_volatility_pct=1.09,
        portfolio_beta=1.067,

        var_95_1d_eur=11_572.57,
        cvar_95_1d_eur=16_225.16,

        top5_concentration_pct=40.86,
        effective_positions=19.33,

        analytical_coverage_pct=95.45,
    )


def _delta() -> PortfolioRiskDelta:

    return PortfolioRiskDelta(
        gross_exposure_eur=4_992.09,
        net_exposure_eur=-4_992.09,

        long_exposure_eur=0.0,
        short_exposure_eur=4_992.09,

        portfolio_volatility_pct=0.0,
        portfolio_beta=0.0,

        var_95_1d_eur=0.0,
        cvar_95_1d_eur=0.0,

        top5_concentration_pct=0.0,
        effective_positions=0.0,

        analytical_coverage_pct=0.0,
    )


def _simulation(
    *,
    simulation_id: str = "SIM-SMCI-DECISION",
    proposal_id: str = "PROP-SMCI-DECISION",
    snapshot_id: str = "SNAP-SMCI-DECISION",
    constraints_passed: bool = True,
    violated_constraints: list[str] | None = None,
    warnings: list[str] | None = None,
) -> PortfolioSimulation:

    if violated_constraints is None:

        violated_constraints = (
            []
            if constraints_passed
            else [
                (
                    "Max trade loss constraint exceeded: "
                    "EUR 7,000.00 > EUR 6,000.00."
                )
            ]
        )

    if warnings is None:

        warnings = []

    return PortfolioSimulation(
        simulation_id=simulation_id,
        snapshot_id=snapshot_id,
        proposal_id=proposal_id,
        created_at=NOW,

        before=_before(),
        after=_after(),
        delta=_delta(),

        cash_after_eur=7_951.03,
        cash_after_usd=53.17,

        constraints_passed=(
            constraints_passed
        ),

        violated_constraints=(
            violated_constraints
        ),

        warnings=warnings,
    )


# =============================================================
# Core decision policy
# =============================================================


def test_reject_when_hard_constraints_fail():

    engine = CioDecisionEngine()

    proposal = _proposal(
        estimated_max_loss_eur=7_000,
    )

    simulation = _simulation(
        constraints_passed=False,
    )

    decision = engine.decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert (
        decision.decision
        == CioDecisionType.REJECT
    )

    assert (
        decision.hard_constraints_passed
        is False
    )

    assert (
        decision.critical_evidence_complete
        is True
    )

    assert (
        decision.required_changes
        == []
    )

    assert any(
        assessment.outcome.value
        == "FAIL"
        for assessment
        in decision.assessments
    )


def test_modify_when_max_loss_is_unknown():

    engine = CioDecisionEngine()

    proposal = _proposal(
        estimated_max_loss_eur=None,
    )

    simulation = _simulation(
        constraints_passed=True,
        warnings=[
            (
                "Trade loss constraint cannot be fully "
                "evaluated because estimated_max_loss_eur "
                "is UNKNOWN."
            ),
        ],
    )

    decision = engine.decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert (
        decision.decision
        == CioDecisionType.MODIFY
    )

    assert (
        decision.hard_constraints_passed
        is True
    )

    assert (
        decision.critical_evidence_complete
        is False
    )

    assert any(
        change.change_type
        == RequiredChangeType.ADD_STOP
        for change
        in decision.required_changes
    )

    assert any(
        assessment.code
        == "MAX_LOSS_UNKNOWN"
        for assessment
        in decision.assessments
    )


def test_accept_when_constraints_pass_and_critical_evidence_is_complete():

    engine = CioDecisionEngine()

    proposal = _proposal(
        estimated_max_loss_eur=2_500,
    )

    simulation = _simulation(
        constraints_passed=True,
        warnings=[],
    )

    decision = engine.decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert (
        decision.decision
        == CioDecisionType.ACCEPT
    )

    assert (
        decision.hard_constraints_passed
        is True
    )

    assert (
        decision.critical_evidence_complete
        is True
    )

    assert (
        decision.required_changes
        == []
    )


# =============================================================
# Simulator warnings
# =============================================================


def test_noncritical_recompute_warnings_do_not_block_accept():

    engine = CioDecisionEngine()

    proposal = _proposal(
        estimated_max_loss_eur=2_500,
    )

    simulation = _simulation(
        constraints_passed=True,
        warnings=[
            (
                "Portfolio beta AFTER is not yet "
                "recomputed by PortfolioRiskSimulator V1."
            ),
            (
                "VaR and CVaR AFTER are not yet "
                "recomputed by PortfolioRiskSimulator V1."
            ),
        ],
    )

    decision = engine.decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert (
        decision.decision
        == CioDecisionType.ACCEPT
    )

    assert len(
        decision.warnings
    ) == 2


def test_critical_unknown_warning_causes_modify():

    engine = CioDecisionEngine()

    proposal = _proposal(
        estimated_max_loss_eur=2_500,
    )

    simulation = _simulation(
        constraints_passed=True,
        warnings=[
            (
                "Max gross exposure % cannot yet be evaluated "
                "because canonical account NAV/equity is not "
                "available in AccountState."
            ),
        ],
    )

    decision = engine.decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert (
        decision.decision
        == CioDecisionType.MODIFY
    )

    assert (
        decision.critical_evidence_complete
        is False
    )

    assert decision.required_changes


# =============================================================
# Provenance / consistency
# =============================================================


def test_rejects_proposal_simulation_id_mismatch():

    engine = CioDecisionEngine()

    proposal = _proposal(
        proposal_id="PROP-A",
        estimated_max_loss_eur=2_500,
    )

    simulation = _simulation(
        proposal_id="PROP-B",
    )

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):

        engine.decide(
            proposal=proposal,
            simulation=simulation,
        )


def test_rejects_snapshot_mismatch():

    engine = CioDecisionEngine()

    proposal = _proposal(
        snapshot_id="SNAP-A",
        estimated_max_loss_eur=2_500,
    )

    simulation = _simulation(
        snapshot_id="SNAP-B",
    )

    with pytest.raises(
        ValueError,
        match="snapshot_id",
    ):

        engine.decide(
            proposal=proposal,
            simulation=simulation,
        )


# =============================================================
# Decision provenance
# =============================================================


def test_decision_carries_proposal_simulation_and_snapshot_ids():

    engine = CioDecisionEngine()

    proposal = _proposal(
        estimated_max_loss_eur=2_500,
    )

    simulation = _simulation()

    decision = engine.decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert (
        decision.proposal_id
        == proposal.proposal_id
    )

    assert (
        decision.simulation_id
        == simulation.simulation_id
    )

    assert (
        decision.snapshot_id
        == proposal.snapshot_id
    )

    assert (
        decision.opportunity_id
        == proposal.opportunity_id
    )


def test_decision_id_is_generated():

    engine = CioDecisionEngine()

    decision = engine.decide(
        proposal=_proposal(
            estimated_max_loss_eur=2_500,
        ),
        simulation=_simulation(),
    )

    assert (
        decision.decision_id
        .startswith(
            "DEC-SMCI-"
        )
    )


# =============================================================
# Decision Engine V1.1 regression
# =============================================================


def test_trade_loss_unknown_is_not_duplicated_as_analytical_warning():

    engine = CioDecisionEngine()

    proposal = _proposal(
        estimated_max_loss_eur=None,
    )

    simulation = _simulation(
        constraints_passed=True,
        warnings=[
            (
                "Trade loss constraint cannot be fully "
                "evaluated because estimated_max_loss_eur "
                "is UNKNOWN."
            ),
        ],
    )

    decision = engine.decide(
        proposal=proposal,
        simulation=simulation,
    )

    max_loss_assessments = [
        assessment
        for assessment in decision.assessments
        if assessment.code == "MAX_LOSS_UNKNOWN"
    ]

    assert len(max_loss_assessments) == 1

    duplicate_warning_assessments = [
        assessment
        for assessment in decision.assessments
        if (
            assessment.evidence_type.value
            == "ANALYTICAL_WARNING"
            and (
                "estimated_max_loss_eur"
                in assessment.summary
                or "trade loss constraint"
                in assessment.summary.lower()
            )
        )
    ]

    assert duplicate_warning_assessments == []


def test_missing_portfolio_nav_has_explicit_required_change():

    engine = CioDecisionEngine()

    proposal = _proposal(
        estimated_max_loss_eur=2_500,
    )

    simulation = _simulation(
        constraints_passed=True,
        warnings=[
            (
                "Max gross exposure % cannot yet be evaluated "
                "because canonical account NAV/equity is not "
                "available in AccountState."
            ),
        ],
    )

    decision = engine.decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert (
        decision.decision
        == CioDecisionType.MODIFY
    )

    assert any(
        assessment.code
        == "PORTFOLIO_NAV_UNKNOWN"
        for assessment
        in decision.assessments
    )

    assert any(
        change.change_type
        == RequiredChangeType.PROVIDE_PORTFOLIO_NAV
        for change
        in decision.required_changes
    )