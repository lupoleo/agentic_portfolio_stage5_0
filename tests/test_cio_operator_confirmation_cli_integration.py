from __future__ import annotations

from datetime import datetime, timezone

import app.cio.cli as cli_module
from app.cio.cio_decision_engine import CioDecisionEngine
from app.cio.execution_plan_builder import ExecutionPlanBuilder
from app.cio.models import (
    CacheStatus,
    Currency,
    DataSource,
    Direction,
    ExecutionPlanStatus,
    ExecutionSide,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentType,
    OpportunityStatus,
    OperatorConfirmationOutcome,
    PortfolioRiskDelta,
    PortfolioRiskState,
    PortfolioSimulation,
    PortfolioSnapshot,
    TradeOpportunity,
    TradeProposal,
    TradingHorizon,
    TradingMode,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    27,
    19,
    45,
    tzinfo=timezone.utc,
)


def _feed_inputs(
    monkeypatch,
    values: list[str],
) -> None:

    iterator = iter(values)

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt="": next(iterator),
    )


def _seed_waiting_execution_plan(
    db_path,
    *,
    suffix: str,
):
    """
    Seed a complete persisted analytical chain using the real Stage3Store.

    No Store methods are mocked. The resulting ExecutionPlan is persisted
    through the normal Stage 3 referential-integrity path.
    """

    store = Stage3Store(
        db_path
    )

    snapshot_id = f"SNAP-E2E-{suffix}"
    opportunity_id = f"OPP-E2E-{suffix}"
    proposal_id = f"PROP-E2E-{suffix}"
    simulation_id = f"SIM-E2E-{suffix}"
    instrument_id = f"FIN-E2E-{suffix}"

    snapshot = PortfolioSnapshot(
        snapshot_id=snapshot_id,
        timestamp=NOW,
        source_file="tests/fixture-portfolio.xlsx",
        source_file_hash=f"HASH-E2E-{suffix}",
        quant_engine_version="TEST",
        analyzed_positions=1,
        gross_exposure_eur=10_000.0,
        net_exposure_eur=10_000.0,
        account_state_id=None,
    )

    store.save_portfolio_snapshot(
        snapshot
    )

    opportunity = TradeOpportunity(
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        created_at=NOW,
        updated_at=NOW,
        ticker="TEST",
        direction=Direction.LONG,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        confidence=0.80,
        target_exposure_eur=860.0,
        max_intended_loss_eur=43.0,
        thesis="Integration-test opportunity.",
        catalyst=None,
        key_risks=[],
        evidence_ids=[],
        status=OpportunityStatus.READY_FOR_PROPOSAL,
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=1,
        rejection_reason=None,
        expiry_reason=None,
        notes="SQLite CLI integration test.",
    )

    store.save_trade_opportunity(
        opportunity
    )

    instrument = FinecoInstrument(
        instrument_id=instrument_id,
        underlying="TEST",
        reference_underlying="TEST",
        exposure_relationship=ExposureRelationship.DIRECT,
        description="Test Ordinary NASDAQ",
        instrument_type=InstrumentType.ORDINARY,
        trading_mode=TradingMode.ORDINARY,
        fineco_symbol="TEST",
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

    store.save_fineco_instrument(
        instrument
    )

    proposal = TradeProposal(
        proposal_id=proposal_id,
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        created_at=NOW,
        ticker="TEST",
        direction=Direction.LONG,
        instrument_id=instrument_id,
        sizing_id=f"SIZ-E2E-{suffix}",
        execution_side=ExecutionSide.BUY,
        quantity=10,
        reference_price=100.0,
        currency=Currency.USD,
        fx_to_eur=0.86,
        entry_type="MARKET",
        entry_price=None,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        gross_exposure_eur=860.0,
        estimated_capital_required_eur=860.0,
        estimated_margin_eur=None,
        estimated_max_loss_eur=43.0,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
    )

    store.save_trade_proposal(
        proposal
    )

    before = PortfolioRiskState(
        gross_exposure_eur=10_000.0,
        net_exposure_eur=10_000.0,
        long_exposure_eur=10_000.0,
        short_exposure_eur=0.0,
        portfolio_volatility_pct=10.0,
        portfolio_beta=1.0,
        var_95_1d_eur=200.0,
        cvar_95_1d_eur=300.0,
        top5_concentration_pct=50.0,
        effective_positions=5.0,
        analytical_coverage_pct=100.0,
    )

    after = PortfolioRiskState(
        gross_exposure_eur=10_860.0,
        net_exposure_eur=10_860.0,
        long_exposure_eur=10_860.0,
        short_exposure_eur=0.0,
        portfolio_volatility_pct=10.0,
        portfolio_beta=1.0,
        var_95_1d_eur=200.0,
        cvar_95_1d_eur=300.0,
        top5_concentration_pct=50.0,
        effective_positions=5.0,
        analytical_coverage_pct=100.0,
    )

    delta = PortfolioRiskDelta(
        gross_exposure_eur=860.0,
        net_exposure_eur=860.0,
        long_exposure_eur=860.0,
        short_exposure_eur=0.0,
        portfolio_volatility_pct=0.0,
        portfolio_beta=0.0,
        var_95_1d_eur=0.0,
        cvar_95_1d_eur=0.0,
        top5_concentration_pct=0.0,
        effective_positions=0.0,
        analytical_coverage_pct=0.0,
    )

    simulation = PortfolioSimulation(
        simulation_id=simulation_id,
        snapshot_id=snapshot_id,
        proposal_id=proposal_id,
        created_at=NOW,
        before=before,
        after=after,
        delta=delta,
        cash_after_eur=5_000.0,
        cash_after_usd=1_000.0,
        constraints_passed=True,
        violated_constraints=[],
        warnings=[],
    )

    store.save_portfolio_simulation(
        simulation
    )

    decision = CioDecisionEngine().decide(
        proposal=proposal,
        simulation=simulation,
    )

    assert decision.decision.value == "ACCEPT"

    store.save_cio_decision(
        decision
    )

    plan = ExecutionPlanBuilder().build(
        proposal=proposal,
        simulation=simulation,
        decision=decision,
        instrument=instrument,
        execution_notes="Real SQLite CLI integration test.",
    )

    store.save_execution_plan(
        plan
    )

    persisted = store.get_execution_plan(
        plan.execution_plan_id
    )

    assert persisted is not None
    assert (
        persisted.status
        == ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
    )

    return opportunity_id, plan.execution_plan_id


def test_cli_confirm_executed_round_trip_with_real_sqlite(
    tmp_path,
    monkeypatch,
    capsys,
):

    db = tmp_path / "cio.db"

    (
        opportunity_id,
        execution_plan_id,
    ) = _seed_waiting_execution_plan(
        db,
        suffix="EXECUTED",
    )

    _feed_inputs(
        monkeypatch,
        [
            "EXECUTED",
            "10",
            "101.25",
            "4.95",
            "BROKER-E2E-001",
            "Executed in integration test.",
        ],
    )

    cli_module.main(
        [
            "--db",
            str(db),
            "opportunities",
            "confirm",
            opportunity_id,
        ]
    )

    out = capsys.readouterr().out

    assert "Outcome:          EXECUTED" in out
    assert "Current status:  OPERATOR_CONFIRMED" in out

    # Fresh store instance: prove the state actually round-tripped via SQLite.
    reloaded = Stage3Store(
        db
    )

    plan = reloaded.get_execution_plan(
        execution_plan_id
    )

    assert plan is not None
    assert (
        plan.status
        == ExecutionPlanStatus.OPERATOR_CONFIRMED
    )

    confirmation = (
        reloaded
        .get_latest_operator_confirmation_for_execution_plan(
            execution_plan_id
        )
    )

    assert confirmation is not None
    assert (
        confirmation.outcome
        == OperatorConfirmationOutcome.EXECUTED
    )
    assert confirmation.executed_quantity == 10
    assert confirmation.executed_price == 101.25
    assert confirmation.commission_eur == 4.95
    assert (
        confirmation.broker_order_reference
        == "BROKER-E2E-001"
    )

    # Read side through the real CLI and real SQLite.
    cli_module.main(
        [
            "--db",
            str(db),
            "opportunities",
            "confirmation",
            opportunity_id,
        ]
    )

    read_out = capsys.readouterr().out

    assert "CIO OPERATOR CONFIRMATION" in read_out
    assert "Outcome:          EXECUTED" in read_out
    assert "Executed price:   101.25" in read_out


def test_cli_confirm_cancelled_round_trip_with_real_sqlite(
    tmp_path,
    monkeypatch,
    capsys,
):

    db = tmp_path / "cio.db"

    (
        opportunity_id,
        execution_plan_id,
    ) = _seed_waiting_execution_plan(
        db,
        suffix="CANCELLED",
    )

    _feed_inputs(
        monkeypatch,
        [
            "CANCELLED",
            "Cancelled in integration test.",
        ],
    )

    cli_module.main(
        [
            "--db",
            str(db),
            "opportunities",
            "confirm",
            opportunity_id,
        ]
    )

    out = capsys.readouterr().out

    assert "Outcome:          CANCELLED" in out
    assert "Current status:  CANCELLED" in out

    reloaded = Stage3Store(
        db
    )

    plan = reloaded.get_execution_plan(
        execution_plan_id
    )

    assert plan is not None
    assert (
        plan.status
        == ExecutionPlanStatus.CANCELLED
    )

    confirmation = (
        reloaded
        .get_latest_operator_confirmation_for_execution_plan(
            execution_plan_id
        )
    )

    assert confirmation is not None
    assert (
        confirmation.outcome
        == OperatorConfirmationOutcome.CANCELLED
    )
    assert confirmation.executed_quantity is None
    assert confirmation.executed_price is None

    cli_module.main(
        [
            "--db",
            str(db),
            "opportunities",
            "confirmation",
            opportunity_id,
        ]
    )

    read_out = capsys.readouterr().out

    assert "CIO OPERATOR CONFIRMATION" in read_out
    assert "Outcome:          CANCELLED" in read_out
