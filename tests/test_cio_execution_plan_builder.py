from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.cio.cio_decision_engine import CioDecisionEngine
from app.cio.execution_plan_builder import ExecutionPlanBuilder
from app.cio.models import (
    CacheStatus,
    CioDecisionType,
    Currency,
    DataSource,
    Direction,
    ExecutionPlanStatus,
    ExecutionSide,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentType,
    PortfolioRiskDelta,
    PortfolioRiskState,
    PortfolioSimulation,
    TradeProposal,
    TradingMode,
)


NOW = datetime(
    2026,
    8,
    25,
    18,
    0,
    tzinfo=timezone.utc,
)


# =============================================================
# Fixtures
# =============================================================


def _proposal(
    *,
    proposal_id: str = "PROP-SMCI-EXEC",
    opportunity_id: str = "OPP-SMCI-EXEC",
    snapshot_id: str = "SNAP-SMCI-EXEC",
    instrument_id: str = "FIN-SMCI-EXEC",
) -> TradeProposal:

    return TradeProposal(
        proposal_id=proposal_id,
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        created_at=NOW,
        ticker="SMCI",
        direction=Direction.SHORT,
        instrument_id=instrument_id,
        sizing_id="SIZ-SMCI-EXEC",
        execution_side=ExecutionSide.SELL_SHORT,
        quantity=429,
        reference_price=37.24,
        currency=Currency.USD,
        fx_to_eur=0.86,
        entry_type="MARKET",
        entry_price=None,
        stop_price=39.0,
        target_1=35.0,
        target_2=33.0,
        gross_exposure_eur=13_739.33,
        estimated_capital_required_eur=13_739.33,
        estimated_margin_eur=None,
        estimated_max_loss_eur=649.33,
        expected_holding_min_days=4,
        expected_holding_max_days=10,
    )


def _before() -> PortfolioRiskState:

    return PortfolioRiskState(
        gross_exposure_eur=263_691.21,
        net_exposure_eur=263_691.21,
        long_exposure_eur=263_691.21,
        short_exposure_eur=0.0,
        portfolio_volatility_pct=17.53,
        portfolio_beta=0.865,
        var_95_1d_eur=4_827.46,
        cvar_95_1d_eur=6_574.16,
        top5_concentration_pct=45.82,
        effective_positions=14.70,
        analytical_coverage_pct=93.18,
    )


def _after() -> PortfolioRiskState:

    return PortfolioRiskState(
        gross_exposure_eur=277_430.54,
        net_exposure_eur=249_951.88,
        long_exposure_eur=263_691.21,
        short_exposure_eur=13_739.33,
        portfolio_volatility_pct=17.53,
        portfolio_beta=0.865,
        var_95_1d_eur=4_827.46,
        cvar_95_1d_eur=6_574.16,
        top5_concentration_pct=45.82,
        effective_positions=14.70,
        analytical_coverage_pct=93.18,
    )


def _delta() -> PortfolioRiskDelta:

    return PortfolioRiskDelta(
        gross_exposure_eur=13_739.33,
        net_exposure_eur=-13_739.33,
        long_exposure_eur=0.0,
        short_exposure_eur=13_739.33,
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
    simulation_id: str = "SIM-SMCI-EXEC",
    proposal_id: str = "PROP-SMCI-EXEC",
    snapshot_id: str = "SNAP-SMCI-EXEC",
    constraints_passed: bool = True,
) -> PortfolioSimulation:

    return PortfolioSimulation(
        simulation_id=simulation_id,
        snapshot_id=snapshot_id,
        proposal_id=proposal_id,
        created_at=NOW,
        before=_before(),
        after=_after(),
        delta=_delta(),
        cash_after_eur=18_182.36,
        cash_after_usd=9_772.02,
        constraints_passed=constraints_passed,
        violated_constraints=(
            []
            if constraints_passed
            else ["Test hard constraint violation."]
        ),
        warnings=[],
    )


def _instrument(
    *,
    instrument_id: str = "FIN-SMCI-EXEC",
) -> FinecoInstrument:

    return FinecoInstrument(
        instrument_id=instrument_id,
        underlying="SMCI",
        reference_underlying="SMCI",
        exposure_relationship=ExposureRelationship.DIRECT,
        description="Super Micro Computer Ordinary NASDAQ",
        instrument_type=InstrumentType.ORDINARY,
        trading_mode=TradingMode.ORDINARY,
        fineco_symbol="SMCI",
        market="NASDAQ",
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        margin_currency=None,
        long_available=True,
        short_available=True,
        intraday_available=True,
        overnight_available=True,
        broker_leverage=1,
        embedded_leverage=1,
        margin_pct=None,
        last_confirmed=NOW,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
    )


def _accepted_decision(
    *,
    proposal: TradeProposal | None = None,
    simulation: PortfolioSimulation | None = None,
):

    if proposal is None:
        proposal = _proposal()

    if simulation is None:
        simulation = _simulation(
            proposal_id=proposal.proposal_id,
            snapshot_id=proposal.snapshot_id,
        )

    decision = CioDecisionEngine().decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert decision.decision == CioDecisionType.ACCEPT

    return decision


# =============================================================
# Successful materialization
# =============================================================


def test_accept_decision_builds_execution_plan():

    proposal = _proposal()
    simulation = _simulation()
    decision = _accepted_decision(
        proposal=proposal,
        simulation=simulation,
    )

    plan = ExecutionPlanBuilder().build(
        proposal=proposal,
        simulation=simulation,
        decision=decision,
        instrument=_instrument(),
    )

    assert plan.opportunity_id == proposal.opportunity_id
    assert plan.proposal_id == proposal.proposal_id
    assert plan.simulation_id == simulation.simulation_id
    assert plan.decision_id == decision.decision_id
    assert plan.snapshot_id == proposal.snapshot_id

    assert plan.broker == "Fineco"
    assert plan.underlying == "SMCI"
    assert plan.instrument_id == "FIN-SMCI-EXEC"
    assert plan.instrument_description == (
        "Super Micro Computer Ordinary NASDAQ"
    )
    assert plan.broker_symbol == "SMCI"
    assert plan.market == "NASDAQ"
    assert plan.currency == Currency.USD

    assert plan.direction == Direction.SHORT
    assert plan.execution_side == ExecutionSide.SELL_SHORT
    assert plan.quantity == 429
    assert plan.order_type == "MARKET"

    assert plan.reference_price == 37.24
    assert plan.entry_price is None
    assert plan.stop_price == 39.0
    assert plan.target_1 == 35.0
    assert plan.target_2 == 33.0
    assert plan.fx_to_eur == 0.86

    assert plan.gross_exposure_eur == 13_739.33
    assert plan.estimated_capital_required_eur == 13_739.33
    assert plan.estimated_max_loss_eur == 649.33

    assert plan.expected_holding_min_days == 4
    assert plan.expected_holding_max_days == 10


def test_new_execution_plan_waits_for_operator_confirmation():

    proposal = _proposal()
    simulation = _simulation()
    decision = _accepted_decision(
        proposal=proposal,
        simulation=simulation,
    )

    plan = ExecutionPlanBuilder().build(
        proposal=proposal,
        simulation=simulation,
        decision=decision,
        instrument=_instrument(),
    )

    assert plan.status == (
        ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
    )


def test_execution_plan_id_is_generated():

    proposal = _proposal()
    simulation = _simulation()
    decision = _accepted_decision(
        proposal=proposal,
        simulation=simulation,
    )

    plan = ExecutionPlanBuilder().build(
        proposal=proposal,
        simulation=simulation,
        decision=decision,
        instrument=_instrument(),
    )

    assert plan.execution_plan_id.startswith(
        "EXEC-SMCI-"
    )


# =============================================================
# CIO governance
# =============================================================


@pytest.mark.parametrize(
    "decision_type",
    [
        CioDecisionType.MODIFY,
        CioDecisionType.REJECT,
    ],
)
def test_non_accept_decision_cannot_build_execution_plan(
    decision_type,
):

    proposal = _proposal()

    if decision_type == CioDecisionType.MODIFY:
        decision_proposal = proposal.model_copy(
            update={
                "estimated_max_loss_eur": None,
            }
        )
        simulation = _simulation(
            proposal_id=decision_proposal.proposal_id,
            snapshot_id=decision_proposal.snapshot_id,
        )
        simulation = simulation.model_copy(
            update={
                "warnings": [
                    (
                        "Trade loss constraint cannot be fully "
                        "evaluated because estimated_max_loss_eur "
                        "is UNKNOWN."
                    )
                ]
            }
        )

    else:
        decision_proposal = proposal.model_copy(
            update={
                "estimated_max_loss_eur": 7_000,
            }
        )
        simulation = _simulation(
            proposal_id=decision_proposal.proposal_id,
            snapshot_id=decision_proposal.snapshot_id,
            constraints_passed=False,
        )

    decision = CioDecisionEngine().decide(
        proposal=decision_proposal,
        simulation=simulation,
    )

    assert decision.decision == decision_type

    with pytest.raises(
        ValueError,
        match="decision=ACCEPT",
    ):
        ExecutionPlanBuilder().build(
            proposal=decision_proposal,
            simulation=simulation,
            decision=decision,
            instrument=_instrument(),
        )


def test_failed_simulation_cannot_build_execution_plan():

    proposal = _proposal()
    simulation = _simulation(
        constraints_passed=False,
    )

    # Build an ACCEPT decision from a passing simulation, then deliberately
    # supply a different failed simulation carrying the same IDs. This
    # isolates the builder's own simulation guard.
    passing_simulation = _simulation()
    decision = _accepted_decision(
        proposal=proposal,
        simulation=passing_simulation,
    )

    with pytest.raises(
        ValueError,
        match="failed constraints",
    ):
        ExecutionPlanBuilder().build(
            proposal=proposal,
            simulation=simulation,
            decision=decision,
            instrument=_instrument(),
        )


# =============================================================
# Provenance integrity
# =============================================================


def test_decision_proposal_id_mismatch_is_rejected():

    proposal = _proposal()
    simulation = _simulation()

    other_proposal = _proposal(
        proposal_id="PROP-OTHER",
    )
    other_simulation = _simulation(
        proposal_id="PROP-OTHER",
    )
    decision = _accepted_decision(
        proposal=other_proposal,
        simulation=other_simulation,
    )

    with pytest.raises(
        ValueError,
        match="proposal_id",
    ):
        ExecutionPlanBuilder().build(
            proposal=proposal,
            simulation=simulation,
            decision=decision,
            instrument=_instrument(),
        )


def test_decision_simulation_id_mismatch_is_rejected():

    proposal = _proposal()
    simulation = _simulation()

    other_simulation = _simulation(
        simulation_id="SIM-OTHER",
    )
    decision = _accepted_decision(
        proposal=proposal,
        simulation=other_simulation,
    )

    with pytest.raises(
        ValueError,
        match="simulation_id",
    ):
        ExecutionPlanBuilder().build(
            proposal=proposal,
            simulation=simulation,
            decision=decision,
            instrument=_instrument(),
        )


def test_decision_snapshot_mismatch_is_rejected():

    proposal = _proposal()
    simulation = _simulation()

    other_proposal = _proposal(
        snapshot_id="SNAP-OTHER",
    )
    other_simulation = _simulation(
        snapshot_id="SNAP-OTHER",
    )
    decision = _accepted_decision(
        proposal=other_proposal,
        simulation=other_simulation,
    )

    with pytest.raises(
        ValueError,
        match="snapshot_id",
    ):
        ExecutionPlanBuilder().build(
            proposal=proposal,
            simulation=simulation,
            decision=decision,
            instrument=_instrument(),
        )


def test_simulation_proposal_id_mismatch_is_rejected():

    proposal = _proposal()
    canonical_simulation = _simulation()
    decision = _accepted_decision(
        proposal=proposal,
        simulation=canonical_simulation,
    )

    mismatched_simulation = canonical_simulation.model_copy(
        update={
            "proposal_id": "PROP-OTHER",
        }
    )

    with pytest.raises(
        ValueError,
        match="proposal_id",
    ):
        ExecutionPlanBuilder().build(
            proposal=proposal,
            simulation=mismatched_simulation,
            decision=decision,
            instrument=_instrument(),
        )


def test_simulation_snapshot_id_mismatch_is_rejected():

    proposal = _proposal()
    canonical_simulation = _simulation()
    decision = _accepted_decision(
        proposal=proposal,
        simulation=canonical_simulation,
    )

    mismatched_simulation = canonical_simulation.model_copy(
        update={
            "snapshot_id": "SNAP-OTHER",
        }
    )

    with pytest.raises(
        ValueError,
        match="snapshot_id",
    ):
        ExecutionPlanBuilder().build(
            proposal=proposal,
            simulation=mismatched_simulation,
            decision=decision,
            instrument=_instrument(),
        )


def test_instrument_mismatch_is_rejected():

    proposal = _proposal()
    simulation = _simulation()
    decision = _accepted_decision(
        proposal=proposal,
        simulation=simulation,
    )

    with pytest.raises(
        ValueError,
        match="instrument_id",
    ):
        ExecutionPlanBuilder().build(
            proposal=proposal,
            simulation=simulation,
            decision=decision,
            instrument=_instrument(
                instrument_id="FIN-OTHER",
            ),
        )