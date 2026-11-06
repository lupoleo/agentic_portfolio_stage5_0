from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.cio.models import (
    CioDecision,
    CioDecisionStatus,
    CioDecisionType,
    Currency,
    Direction,
    ExecutionSide,
    PortfolioRiskDelta,
    PortfolioRiskState,
    PortfolioSimulation,
    PortfolioSnapshot,
    TradeProposal,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    21,
    13,
    0,
    tzinfo=timezone.utc,
)


# =============================================================
# Fixtures
# =============================================================


def _snapshot(
    *,
    snapshot_id: str = "SNAP-DECISION-STORAGE",
) -> PortfolioSnapshot:

    return PortfolioSnapshot(
        snapshot_id=snapshot_id,
        timestamp=NOW,
        source_file=(
            "data/input/portafoglio-export.xlsx"
        ),
        source_file_hash=(
            "HASH-DECISION-STORAGE"
        ),
        quant_engine_version="2.5",
        analyzed_positions=33,
        gross_exposure_eur=371_343.34,
        net_exposure_eur=371_343.34,
        account_state_id=None,
    )


def _proposal(
    *,
    proposal_id: str = "PROP-DECISION-STORAGE",
    opportunity_id: str = "OPP-DECISION-STORAGE",
    snapshot_id: str = "SNAP-DECISION-STORAGE",
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
        sizing_id=(
            "SIZ-DECISION-STORAGE"
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
        estimated_max_loss_eur=None,
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
    simulation_id: str = "SIM-DECISION-STORAGE",
    proposal_id: str = "PROP-DECISION-STORAGE",
    snapshot_id: str = "SNAP-DECISION-STORAGE",
) -> PortfolioSimulation:

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
        constraints_passed=True,
        violated_constraints=[],
        warnings=[
            (
                "Portfolio beta AFTER is not yet "
                "recomputed by PortfolioRiskSimulator V1."
            )
        ],
    )


def _decision(
    *,
    decision_id: str = "DEC-SMCI-STORAGE",
    opportunity_id: str = "OPP-DECISION-STORAGE",
    proposal_id: str = "PROP-DECISION-STORAGE",
    simulation_id: str = "SIM-DECISION-STORAGE",
    snapshot_id: str = "SNAP-DECISION-STORAGE",
    created_at: datetime = NOW,
) -> CioDecision:

    return CioDecision(
        decision_id=decision_id,
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        simulation_id=simulation_id,
        snapshot_id=snapshot_id,
        created_at=created_at,
        decision=CioDecisionType.ACCEPT,
        confidence=0.85,
        rationale=(
            "All enforceable constraints passed and "
            "critical evidence is complete."
        ),
        evidence_ids=[],
        assessments=[],
        required_changes=[],
        warnings=[],
        hard_constraints_passed=True,
        critical_evidence_complete=True,
        status=CioDecisionStatus.PRELIMINARY,
        operator_notes=None,
    )


def _prepare_store(
    tmp_path,
) -> Stage3Store:

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_portfolio_snapshot(
        _snapshot()
    )

    store.save_trade_proposal(
        _proposal()
    )

    store.save_portfolio_simulation(
        _simulation()
    )

    return store


# =============================================================
# Persistence
# =============================================================


def test_save_and_get_cio_decision(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    decision = _decision()

    store.save_cio_decision(
        decision
    )

    loaded = (
        store.get_cio_decision(
            decision.decision_id
        )
    )

    assert loaded is not None

    assert (
        loaded.decision_id
        == "DEC-SMCI-STORAGE"
    )

    assert (
        loaded.proposal_id
        == "PROP-DECISION-STORAGE"
    )

    assert (
        loaded.simulation_id
        == "SIM-DECISION-STORAGE"
    )

    assert (
        loaded.opportunity_id
        == "OPP-DECISION-STORAGE"
    )

    assert (
        loaded.snapshot_id
        == "SNAP-DECISION-STORAGE"
    )

    assert (
        loaded.decision
        == CioDecisionType.ACCEPT
    )


def test_list_cio_decisions_for_opportunity(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    first = _decision(
        decision_id="DEC-001"
    )

    second = _decision(
        decision_id="DEC-002"
    )

    store.save_cio_decision(
        first
    )

    store.save_cio_decision(
        second
    )

    decisions = (
        store.list_cio_decisions(
            "OPP-DECISION-STORAGE"
        )
    )

    assert len(decisions) == 2

    ids = {
        item.decision_id
        for item in decisions
    }

    assert ids == {
        "DEC-001",
        "DEC-002",
    }


def test_list_cio_decisions_for_proposal(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    store.save_cio_decision(
        _decision(
            decision_id="DEC-PROP-001"
        )
    )

    store.save_cio_decision(
        _decision(
            decision_id="DEC-PROP-002"
        )
    )

    decisions = (
        store.list_cio_decisions_for_proposal(
            "PROP-DECISION-STORAGE"
        )
    )

    assert len(decisions) == 2


def test_get_latest_cio_decision(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    old = _decision(
        decision_id="DEC-OLD",
        created_at=datetime(
            2026,
            8,
            21,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )

    new = _decision(
        decision_id="DEC-NEW",
        created_at=datetime(
            2026,
            8,
            21,
            13,
            0,
            tzinfo=timezone.utc,
        ),
    )

    store.save_cio_decision(
        old
    )

    store.save_cio_decision(
        new
    )

    latest = (
        store.get_latest_cio_decision(
            "OPP-DECISION-STORAGE"
        )
    )

    assert latest is not None

    assert (
        latest.decision_id
        == "DEC-NEW"
    )


def test_get_latest_cio_decision_for_proposal(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    old = _decision(
        decision_id="DEC-PROP-OLD",
        created_at=datetime(
            2026,
            8,
            21,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )

    new = _decision(
        decision_id="DEC-PROP-NEW",
        created_at=datetime(
            2026,
            8,
            21,
            13,
            0,
            tzinfo=timezone.utc,
        ),
    )

    store.save_cio_decision(
        old
    )

    store.save_cio_decision(
        new
    )

    latest = (
        store.get_latest_cio_decision_for_proposal(
            "PROP-DECISION-STORAGE"
        )
    )

    assert latest is not None

    assert (
        latest.decision_id
        == "DEC-PROP-NEW"
    )


# =============================================================
# Referential integrity
# =============================================================


def test_cannot_save_decision_without_proposal(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    decision = _decision()

    with pytest.raises(
        ValueError,
        match="TradeProposal not found",
    ):

        store.save_cio_decision(
            decision
        )


def test_cannot_save_decision_without_simulation(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_portfolio_snapshot(
        _snapshot()
    )

    store.save_trade_proposal(
        _proposal()
    )

    decision = _decision()

    with pytest.raises(
        ValueError,
        match="PortfolioSimulation not found",
    ):

        store.save_cio_decision(
            decision
        )


def test_cannot_save_decision_with_simulation_for_other_proposal(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_portfolio_snapshot(
        _snapshot()
    )

    store.save_trade_proposal(
        _proposal(
            proposal_id="PROP-A"
        )
    )

    store.save_trade_proposal(
        _proposal(
            proposal_id="PROP-B"
        )
    )

    store.save_portfolio_simulation(
        _simulation(
            simulation_id="SIM-B",
            proposal_id="PROP-B",
        )
    )

    decision = _decision(
        proposal_id="PROP-A",
        simulation_id="SIM-B",
    )

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):

        store.save_cio_decision(
            decision
        )


def test_cannot_save_decision_with_wrong_opportunity_provenance(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    decision = _decision(
        opportunity_id="OPP-WRONG"
    )

    with pytest.raises(
        ValueError,
        match="opportunity_id",
    ):

        store.save_cio_decision(
            decision
        )


def test_cannot_save_decision_with_wrong_snapshot_provenance(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    decision = _decision(
        snapshot_id="SNAP-WRONG"
    )

    with pytest.raises(
        ValueError,
        match="snapshot_id",
    ):

        store.save_cio_decision(
            decision
        )