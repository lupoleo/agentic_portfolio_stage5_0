from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.cio.models import (
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
    20,
    14,
    0,
    tzinfo=timezone.utc,
)


# =============================================================
# Fixtures
# =============================================================


def _snapshot(
    *,
    snapshot_id: str = "SNAP-SIM-STORAGE",
) -> PortfolioSnapshot:

    return PortfolioSnapshot(
        snapshot_id=snapshot_id,
        timestamp=NOW,
        source_file=(
            "data/input/portafoglio-export.xlsx"
        ),
        source_file_hash="TEST-HASH-SIM-STORAGE",
        quant_engine_version="2.5",
        analyzed_positions=32,
        gross_exposure_eur=364_531.64,
        net_exposure_eur=364_531.64,
        account_state_id=None,
    )


def _proposal(
    *,
    proposal_id: str = "PROP-SMCI-STORAGE",
    snapshot_id: str = "SNAP-SIM-STORAGE",
) -> TradeProposal:

    return TradeProposal(
        proposal_id=proposal_id,
        opportunity_id="OPP-SMCI-STORAGE",
        snapshot_id=snapshot_id,
        created_at=NOW,

        ticker="SMCI",
        direction=Direction.SHORT,

        instrument_id="FIN-SMCI-ORDINARY",

        sizing_id="SIZ-SMCI-STORAGE",

        execution_side=(
            ExecutionSide.SELL_SHORT
        ),

        quantity=158,
        reference_price=36.72,
        currency=Currency.USD,

        entry_type="MARKET",

        entry_price=None,
        stop_price=None,
        target_1=None,
        target_2=None,

        gross_exposure_eur=4_989.51,

        estimated_margin_eur=None,
        estimated_max_loss_eur=None,

        expected_holding_min_days=5,
        expected_holding_max_days=15,
    )


def _before() -> PortfolioRiskState:

    return PortfolioRiskState(
        gross_exposure_eur=364_531.64,
        net_exposure_eur=364_531.64,

        long_exposure_eur=364_531.64,
        short_exposure_eur=0.0,

        portfolio_volatility_pct=18.0,
        portfolio_beta=0.97,

        var_95_1d_eur=7_276.68,
        cvar_95_1d_eur=10_598.29,

        top5_concentration_pct=42.0,
        effective_positions=18.0,

        analytical_coverage_pct=96.93,
    )


def _after() -> PortfolioRiskState:

    return PortfolioRiskState(
        gross_exposure_eur=369_521.15,
        net_exposure_eur=359_542.13,

        long_exposure_eur=364_531.64,
        short_exposure_eur=4_989.51,

        portfolio_volatility_pct=18.0,
        portfolio_beta=0.97,

        var_95_1d_eur=7_276.68,
        cvar_95_1d_eur=10_598.29,

        top5_concentration_pct=42.0,
        effective_positions=18.0,

        analytical_coverage_pct=96.93,
    )


def _delta() -> PortfolioRiskDelta:

    return PortfolioRiskDelta(
        gross_exposure_eur=4_989.51,
        net_exposure_eur=-4_989.51,

        long_exposure_eur=0.0,
        short_exposure_eur=4_989.51,

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
    simulation_id: str = "SIM-SMCI-STORAGE",
    proposal_id: str = "PROP-SMCI-STORAGE",
    snapshot_id: str = "SNAP-SIM-STORAGE",
    created_at: datetime = NOW,
    constraints_passed: bool = True,
) -> PortfolioSimulation:

    return PortfolioSimulation(
        simulation_id=simulation_id,
        snapshot_id=snapshot_id,
        proposal_id=proposal_id,
        created_at=created_at,

        before=_before(),
        after=_after(),
        delta=_delta(),

        cash_after_eur=7_951.03,
        cash_after_usd=53.17,

        constraints_passed=(
            constraints_passed
        ),

        violated_constraints=(
            []
            if constraints_passed
            else [
                "Max trade loss constraint exceeded"
            ]
        ),

        warnings=[
            "Portfolio beta AFTER is not yet recomputed.",
        ],
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

    return store


# =============================================================
# Persistence
# =============================================================


def test_save_and_get_portfolio_simulation(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    simulation = _simulation()

    store.save_portfolio_simulation(
        simulation
    )

    loaded = (
        store.get_portfolio_simulation(
            simulation.simulation_id
        )
    )

    assert loaded is not None

    assert (
        loaded.simulation_id
        == "SIM-SMCI-STORAGE"
    )

    assert (
        loaded.proposal_id
        == "PROP-SMCI-STORAGE"
    )

    assert (
        loaded.snapshot_id
        == "SNAP-SIM-STORAGE"
    )

    assert loaded.constraints_passed

    assert (
        loaded.after.short_exposure_eur
        == pytest.approx(
            4_989.51,
            abs=0.01,
        )
    )

    assert (
        loaded.delta.net_exposure_eur
        == pytest.approx(
            -4_989.51,
            abs=0.01,
        )
    )


def test_list_portfolio_simulations(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    first = _simulation(
        simulation_id="SIM-001"
    )

    second = _simulation(
        simulation_id="SIM-002"
    )

    store.save_portfolio_simulation(
        first
    )

    store.save_portfolio_simulation(
        second
    )

    simulations = (
        store.list_portfolio_simulations(
            "PROP-SMCI-STORAGE"
        )
    )

    assert len(simulations) == 2

    ids = {
        simulation.simulation_id
        for simulation in simulations
    }

    assert ids == {
        "SIM-001",
        "SIM-002",
    }


def test_get_latest_portfolio_simulation(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    first = _simulation(
        simulation_id="SIM-OLD",
        created_at=datetime(
            2026,
            8,
            20,
            14,
            0,
            tzinfo=timezone.utc,
        ),
    )

    second = _simulation(
        simulation_id="SIM-NEW",
        created_at=datetime(
            2026,
            8,
            20,
            14,
            30,
            tzinfo=timezone.utc,
        ),
    )

    store.save_portfolio_simulation(
        first
    )

    store.save_portfolio_simulation(
        second
    )

    latest = (
        store.get_latest_portfolio_simulation(
            "PROP-SMCI-STORAGE"
        )
    )

    assert latest is not None

    assert (
        latest.simulation_id
        == "SIM-NEW"
    )


# =============================================================
# Referential integrity
# =============================================================


def test_cannot_save_simulation_without_proposal(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_portfolio_snapshot(
        _snapshot()
    )

    simulation = _simulation()

    with pytest.raises(
        ValueError,
        match="TradeProposal not found",
    ):

        store.save_portfolio_simulation(
            simulation
        )


def test_cannot_save_simulation_without_snapshot(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_trade_proposal(
        _proposal()
    )

    simulation = _simulation()

    with pytest.raises(
        ValueError,
        match="PortfolioSnapshot not found",
    ):

        store.save_portfolio_simulation(
            simulation
        )


def test_cannot_save_simulation_with_snapshot_mismatch(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_portfolio_snapshot(
        _snapshot(
            snapshot_id="SNAP-OTHER"
        )
    )

    store.save_trade_proposal(
        _proposal(
            snapshot_id="SNAP-PROPOSAL"
        )
    )

    simulation = _simulation(
        snapshot_id="SNAP-OTHER"
    )

    with pytest.raises(
        ValueError,
        match="snapshot_id does not match",
    ):

        store.save_portfolio_simulation(
            simulation
        )


# =============================================================
# Failed simulations are still valid evidence
# =============================================================


def test_failed_constraint_simulation_is_persisted(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    simulation = _simulation(
        simulation_id="SIM-FAILED",
        constraints_passed=False,
    )

    store.save_portfolio_simulation(
        simulation
    )

    loaded = (
        store.get_portfolio_simulation(
            "SIM-FAILED"
        )
    )

    assert loaded is not None

    assert not loaded.constraints_passed

    assert (
        loaded.violated_constraints
        == [
            "Max trade loss constraint exceeded"
        ]
    )