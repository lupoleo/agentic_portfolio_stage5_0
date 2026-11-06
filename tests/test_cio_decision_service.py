from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.cio.cio_decision_service import (
    CioDecisionService,
)
from app.cio.models import (
    CioDecisionType,
    Currency,
    Direction,
    ExecutionSide,
    OpportunityStatus,
    PortfolioRiskDelta,
    PortfolioRiskState,
    PortfolioSimulation,
    PortfolioSnapshot,
    TradeOpportunity,
    TradeProposal,
    TradingHorizon,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    21,
    14,
    0,
    tzinfo=timezone.utc,
)


# =============================================================
# Fixtures
# =============================================================


def _snapshot(
    *,
    snapshot_id: str = "SNAP-DECISION-SERVICE",
) -> PortfolioSnapshot:

    return PortfolioSnapshot(
        snapshot_id=snapshot_id,
        timestamp=NOW,
        source_file=(
            "data/input/portafoglio-export.xlsx"
        ),
        source_file_hash=(
            "HASH-DECISION-SERVICE"
        ),
        quant_engine_version="2.5",
        analyzed_positions=33,
        gross_exposure_eur=371_343.34,
        net_exposure_eur=371_343.34,
        account_state_id=None,
    )


def _opportunity(
    *,
    opportunity_id: str = "OPP-DECISION-SERVICE",
    snapshot_id: str = "SNAP-DECISION-SERVICE",
    status: OpportunityStatus = (
        OpportunityStatus.READY_FOR_PROPOSAL
    ),
) -> TradeOpportunity:

    return TradeOpportunity(
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        created_at=NOW,
        updated_at=NOW,
        ticker="SMCI",
        direction=Direction.SHORT,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=4,
        expected_holding_max_days=12,
        confidence=0.75,
        target_exposure_eur=5_000.0,
        max_intended_loss_eur=6_000.0,
        thesis=(
            "Bearish swing thesis on SMCI."
        ),
        catalyst=None,
        key_risks=[],
        evidence_ids=[],
        status=status,
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=10,
        rejection_reason=None,
        expiry_reason=None,
        notes=None,
    )


def _proposal(
    *,
    proposal_id: str = "PROP-DECISION-SERVICE",
    opportunity_id: str = "OPP-DECISION-SERVICE",
    snapshot_id: str = "SNAP-DECISION-SERVICE",
    created_at: datetime = NOW,
    estimated_max_loss_eur: float | None = None,
) -> TradeProposal:

    return TradeProposal(
        proposal_id=proposal_id,
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        created_at=created_at,
        ticker="SMCI",
        direction=Direction.SHORT,
        instrument_id=(
            "FIN-SMCI-ORDINARY-ORDINARY-X1"
        ),
        sizing_id=(
            "SIZ-DECISION-SERVICE"
        ),
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
        expected_holding_min_days=4,
        expected_holding_max_days=12,
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
    simulation_id: str = "SIM-DECISION-SERVICE",
    proposal_id: str = "PROP-DECISION-SERVICE",
    snapshot_id: str = "SNAP-DECISION-SERVICE",
    constraints_passed: bool = True,
    warnings: list[str] | None = None,
) -> PortfolioSimulation:

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
        warnings=(
            warnings
            if warnings is not None
            else []
        ),
    )


def _prepare_store(
    tmp_path,
    *,
    proposal: TradeProposal | None = None,
    simulation: PortfolioSimulation | None = None,
    opportunity: TradeOpportunity | None = None,
) -> Stage3Store:

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    snapshot = _snapshot()

    store.save_portfolio_snapshot(
        snapshot
    )

    store.save_trade_opportunity(
        opportunity
        or _opportunity()
    )

    proposal = (
        proposal
        or _proposal()
    )

    store.save_trade_proposal(
        proposal
    )

    if simulation is not None:

        store.save_portfolio_simulation(
            simulation
        )

    return store


# =============================================================
# Successful orchestration
# =============================================================


def test_create_decision_persists_modify(
    tmp_path,
):

    proposal = _proposal(
        estimated_max_loss_eur=None,
    )

    simulation = _simulation(
        warnings=[
            (
                "Trade loss constraint cannot be fully "
                "evaluated because estimated_max_loss_eur "
                "is UNKNOWN."
            ),
        ],
    )

    store = _prepare_store(
        tmp_path,
        proposal=proposal,
        simulation=simulation,
    )

    service = CioDecisionService(
        store
    )

    decision = service.create_decision(
        "OPP-DECISION-SERVICE"
    )

    assert (
        decision.decision
        == CioDecisionType.MODIFY
    )

    persisted = (
        store.get_cio_decision(
            decision.decision_id
        )
    )

    assert persisted is not None

    assert (
        persisted.decision_id
        == decision.decision_id
    )


def test_create_decision_persists_accept(
    tmp_path,
):

    proposal = _proposal(
        estimated_max_loss_eur=2_500.0,
    )

    simulation = _simulation(
        warnings=[],
    )

    store = _prepare_store(
        tmp_path,
        proposal=proposal,
        simulation=simulation,
    )

    decision = (
        CioDecisionService(
            store
        )
        .create_decision(
            "OPP-DECISION-SERVICE"
        )
    )

    assert (
        decision.decision
        == CioDecisionType.ACCEPT
    )

    assert (
        store.get_latest_cio_decision(
            "OPP-DECISION-SERVICE"
        )
        is not None
    )


def test_create_decision_persists_reject(
    tmp_path,
):

    proposal = _proposal(
        estimated_max_loss_eur=7_000.0,
    )

    simulation = _simulation(
        constraints_passed=False,
    )

    store = _prepare_store(
        tmp_path,
        proposal=proposal,
        simulation=simulation,
    )

    decision = (
        CioDecisionService(
            store
        )
        .create_decision(
            "OPP-DECISION-SERVICE"
        )
    )

    assert (
        decision.decision
        == CioDecisionType.REJECT
    )


# =============================================================
# Preconditions
# =============================================================


def test_missing_opportunity_is_rejected(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    service = CioDecisionService(
        store
    )

    with pytest.raises(
        ValueError,
        match="TradeOpportunity not found",
    ):

        service.create_decision(
            "OPP-MISSING"
        )


def test_opportunity_must_be_ready_for_proposal(
    tmp_path,
):

    store = _prepare_store(
        tmp_path,
        opportunity=_opportunity(
            status=(
                OpportunityStatus.POSITION_SIZED
            ),
        ),
        simulation=_simulation(),
    )

    with pytest.raises(
        ValueError,
        match="READY_FOR_PROPOSAL",
    ):

        CioDecisionService(
            store
        ).create_decision(
            "OPP-DECISION-SERVICE"
        )


def test_missing_proposal_is_rejected(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_portfolio_snapshot(
        _snapshot()
    )

    store.save_trade_opportunity(
        _opportunity()
    )

    with pytest.raises(
        ValueError,
        match="No persisted TradeProposal",
    ):

        CioDecisionService(
            store
        ).create_decision(
            "OPP-DECISION-SERVICE"
        )


def test_missing_simulation_is_rejected(
    tmp_path,
):

    store = _prepare_store(
        tmp_path,
        simulation=None,
    )

    with pytest.raises(
        ValueError,
        match="No persisted PortfolioSimulation",
    ):

        CioDecisionService(
            store
        ).create_decision(
            "OPP-DECISION-SERVICE"
        )


# =============================================================
# Exact proposal provenance
# =============================================================


def test_service_does_not_use_simulation_from_older_proposal(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_portfolio_snapshot(
        _snapshot()
    )

    store.save_trade_opportunity(
        _opportunity()
    )

    old_proposal = _proposal(
        proposal_id="PROP-OLD",
        created_at=NOW,
        estimated_max_loss_eur=2_500.0,
    )

    new_proposal = _proposal(
        proposal_id="PROP-NEW",
        created_at=(
            NOW
            + timedelta(
                minutes=10
            )
        ),
        estimated_max_loss_eur=2_500.0,
    )

    store.save_trade_proposal(
        old_proposal
    )

    store.save_trade_proposal(
        new_proposal
    )

    store.save_portfolio_simulation(
        _simulation(
            simulation_id="SIM-OLD",
            proposal_id="PROP-OLD",
        )
    )

    with pytest.raises(
        ValueError,
        match="No persisted PortfolioSimulation",
    ):

        CioDecisionService(
            store
        ).create_decision(
            "OPP-DECISION-SERVICE"
        )


# =============================================================
# Retrieval
# =============================================================


def test_get_latest_decision(
    tmp_path,
):

    proposal = _proposal(
        estimated_max_loss_eur=2_500.0,
    )

    simulation = _simulation()

    store = _prepare_store(
        tmp_path,
        proposal=proposal,
        simulation=simulation,
    )

    service = CioDecisionService(
        store
    )

    created = (
        service.create_decision(
            "OPP-DECISION-SERVICE"
        )
    )

    latest = (
        service.get_latest_decision(
            "OPP-DECISION-SERVICE"
        )
    )

    assert latest is not None

    assert (
        latest.decision_id
        == created.decision_id
    )