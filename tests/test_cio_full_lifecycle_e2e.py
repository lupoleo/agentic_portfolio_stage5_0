from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.cio.cli as cli_module
from app.cio.models import (
    CacheStatus,
    CioDecision,
    CioDecisionStatus,
    CioDecisionType,
    Currency,
    DataSource,
    Direction,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionSide,
    FinecoInstrument,
    InstrumentCandidate,
    InstrumentType,
    OpportunityStatus,
    PortfolioRiskDelta,
    PortfolioRiskState,
    PortfolioSimulation,
    PortfolioSnapshot,
    PositionSizingResult,
    ProposalStatus,
    TradeExitReason,
    TradeOpportunity,
    TradeOutcomeStatus,
    TradeProposal,
    TradingHorizon,
    TradingMode,
)
from app.cio.storage import Stage3Store


OPPORTUNITY_ID = "OPP-E2E-TEST-001"
SNAPSHOT_ID = "SNAP-E2E-TEST-001"
INSTRUMENT_ID = "FIN-E2E-TEST-001"
SIZING_ID = "SIZ-E2E-TEST-001"
PROPOSAL_ID = "PROP-E2E-TEST-001"
SIMULATION_ID = "SIM-E2E-TEST-001"
DECISION_ID = "DEC-E2E-TEST-001"
EXECUTION_PLAN_ID = "EXEC-E2E-TEST-001"


def _feed_inputs(monkeypatch, values: list[str]) -> None:
    iterator = iter(values)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt="": next(iterator),
    )


def _risk_state(
    *,
    gross: float,
    net: float,
    long: float,
    short: float,
) -> PortfolioRiskState:
    return PortfolioRiskState(
        gross_exposure_eur=gross,
        net_exposure_eur=net,
        long_exposure_eur=long,
        short_exposure_eur=short,
        portfolio_volatility_pct=15.0,
        portfolio_beta=0.80,
        var_95_1d_eur=4000.0,
        cvar_95_1d_eur=5200.0,
        top5_concentration_pct=55.0,
        effective_positions=12.0,
        analytical_coverage_pct=100.0,
    )


def _seed_pre_execution_chain(
    store: Stage3Store,
    now: datetime,
) -> None:
    """
    Seed a completely synthetic, internally consistent analytical chain.

    The purpose of this fixture is not to retest every upstream builder.
    It establishes persisted canonical Stage 3 state so the test can
    exercise the human/manual execution boundary through the real CLI and
    then verify the resulting SQLite state end-to-end.
    """

    snapshot = PortfolioSnapshot(
        snapshot_id=SNAPSHOT_ID,
        timestamp=now,
        source_file="synthetic_e2e.xlsx",
        source_file_hash="synthetic-e2e-hash",
        quant_engine_version="test-e2e",
        analyzed_positions=12,
        gross_exposure_eur=100_000.0,
        net_exposure_eur=80_000.0,
        account_state_id=None,
    )
    store.save_portfolio_snapshot(snapshot)

    instrument = FinecoInstrument(
        instrument_id=INSTRUMENT_ID,
        underlying="TEST",
        description="Synthetic TEST Ordinary NASDAQ",
        instrument_type=InstrumentType.ORDINARY,
        trading_mode=TradingMode.ORDINARY,
        fineco_symbol="TEST",
        market="NASDAQ",
        quote_currency=Currency.USD,
        long_available=True,
        short_available=True,
        broker_leverage=1.0,
        embedded_leverage=1.0,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
        last_confirmed=now,
        notes="Synthetic E2E instrument.",
    )
    store.save_fineco_instrument(instrument)

    opportunity = TradeOpportunity(
        opportunity_id=OPPORTUNITY_ID,
        snapshot_id=SNAPSHOT_ID,
        created_at=now,
        updated_at=now,
        ticker="TEST",
        direction=Direction.LONG,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        confidence=0.80,
        target_exposure_eur=860.0,
        max_intended_loss_eur=50.0,
        thesis="Synthetic long opportunity for lifecycle acceptance testing.",
        catalyst="Synthetic catalyst.",
        key_risks=["Synthetic downside risk."],
        status=OpportunityStatus.READY_FOR_INSTRUMENT_SELECTION,
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=1,
        notes="Synthetic E2E opportunity.",
    )
    store.save_trade_opportunity(opportunity)

    candidate = InstrumentCandidate(
        instrument_id=INSTRUMENT_ID,
        opportunity_id=OPPORTUNITY_ID,
        eligible=True,
        suitability_score=0.95,
    )
    store.replace_instrument_candidates(
        OPPORTUNITY_ID,
        [candidate],
    )
    store.mark_trade_opportunity_instruments_ranked(
        OPPORTUNITY_ID
    )

    sizing = PositionSizingResult(
        sizing_id=SIZING_ID,
        opportunity_id=OPPORTUNITY_ID,
        instrument_id=INSTRUMENT_ID,
        created_at=now + timedelta(seconds=1),
        execution_side=ExecutionSide.BUY,
        reference_price=100.0,
        currency=Currency.USD,
        fx_to_eur=0.86,
        stop_price=95.0,
        quantity=10.0,
        gross_exposure_eur=860.0,
        estimated_capital_required_eur=860.0,
        estimated_margin_eur=None,
        estimated_max_loss_eur=43.0,
        risk_budget_eur=50.0,
        constraints_passed=True,
        violated_constraints=[],
        notes="Synthetic sizing.",
    )
    store.save_position_sizing(sizing)
    store.mark_trade_opportunity_position_sized(
        OPPORTUNITY_ID,
        SIZING_ID,
    )

    proposal = TradeProposal(
        proposal_id=PROPOSAL_ID,
        opportunity_id=OPPORTUNITY_ID,
        snapshot_id=SNAPSHOT_ID,
        created_at=now + timedelta(seconds=2),
        ticker="TEST",
        direction=Direction.LONG,
        instrument_id=INSTRUMENT_ID,
        sizing_id=SIZING_ID,
        execution_side=ExecutionSide.BUY,
        quantity=10.0,
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
        status=ProposalStatus.PROPOSED,
    )
    store.save_trade_proposal(proposal)
    store.mark_trade_opportunity_ready_for_proposal(
        OPPORTUNITY_ID,
        PROPOSAL_ID,
    )

    before = _risk_state(
        gross=100_000.0,
        net=80_000.0,
        long=90_000.0,
        short=10_000.0,
    )
    after = _risk_state(
        gross=100_860.0,
        net=80_860.0,
        long=90_860.0,
        short=10_000.0,
    )

    simulation = PortfolioSimulation(
        simulation_id=SIMULATION_ID,
        snapshot_id=SNAPSHOT_ID,
        proposal_id=PROPOSAL_ID,
        created_at=now + timedelta(seconds=3),
        before=before,
        after=after,
        delta=PortfolioRiskDelta(
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
        ),
        cash_after_eur=9_140.0,
        cash_after_usd=1_000.0,
        constraints_passed=True,
        violated_constraints=[],
        warnings=[],
    )
    store.save_portfolio_simulation(simulation)

    decision = CioDecision(
        decision_id=DECISION_ID,
        proposal_id=PROPOSAL_ID,
        simulation_id=SIMULATION_ID,
        opportunity_id=OPPORTUNITY_ID,
        snapshot_id=SNAPSHOT_ID,
        created_at=now + timedelta(seconds=4),
        decision=CioDecisionType.ACCEPT,
        confidence=0.90,
        rationale="Synthetic proposal accepted for lifecycle E2E testing.",
        evidence_ids=[],
        assessments=[],
        required_changes=[],
        warnings=[],
        hard_constraints_passed=True,
        critical_evidence_complete=True,
        status=CioDecisionStatus.PRELIMINARY,
    )
    store.save_cio_decision(decision)

    plan = ExecutionPlan(
        execution_plan_id=EXECUTION_PLAN_ID,
        created_at=now + timedelta(seconds=5),
        opportunity_id=OPPORTUNITY_ID,
        proposal_id=PROPOSAL_ID,
        simulation_id=SIMULATION_ID,
        decision_id=DECISION_ID,
        snapshot_id=SNAPSHOT_ID,
        broker="Fineco",
        underlying="TEST",
        instrument_id=INSTRUMENT_ID,
        instrument_description="Synthetic TEST Ordinary NASDAQ",
        broker_symbol="TEST",
        market="NASDAQ",
        currency=Currency.USD,
        direction=Direction.LONG,
        execution_side=ExecutionSide.BUY,
        quantity=10.0,
        order_type="MARKET",
        reference_price=100.0,
        entry_price=None,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        fx_to_eur=0.86,
        gross_exposure_eur=860.0,
        estimated_capital_required_eur=860.0,
        estimated_max_loss_eur=43.0,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        status=ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION,
        execution_notes="Synthetic manual broker instruction.",
    )
    store.save_execution_plan(plan)


def test_synthetic_manual_execution_to_closed_outcome_e2e(
    tmp_path,
    monkeypatch,
    capsys,
):
    """
    Acceptance test for the Stage 3 manual-execution boundary.

    Uses:
      - a real temporary SQLite database;
      - real Stage3Store persistence;
      - real CLI routing;
      - real OperatorConfirmationService;
      - real TradeOutcomeService / calculator.

    No broker API is called and no real trade is executed.
    """

    db_path = tmp_path / "cio_e2e.db"
    store = Stage3Store(db_path)

    now = datetime.now(timezone.utc)
    _seed_pre_execution_chain(store, now)

    # ---------------------------------------------------------
    # 1. Human reports that the broker instruction was EXECUTED.
    # ---------------------------------------------------------

    _feed_inputs(
        monkeypatch,
        [
            "EXECUTED",
            "10",
            "101",
            "4",
            "SYNTHETIC-BROKER-REF",
            "Synthetic manual execution.",
        ],
    )

    cli_module.main(
        [
            "--db",
            str(db_path),
            "opportunities",
            "confirm",
            OPPORTUNITY_ID,
        ]
    )

    persisted_plan = store.get_execution_plan(
        EXECUTION_PLAN_ID
    )
    assert persisted_plan is not None
    assert (
        persisted_plan.status
        == ExecutionPlanStatus.OPERATOR_CONFIRMED
    )

    confirmations = (
        store.list_operator_confirmations_for_execution_plan(
            EXECUTION_PLAN_ID
        )
    )
    assert len(confirmations) == 1

    confirmation = confirmations[0]
    assert confirmation.executed_quantity == 10.0
    assert confirmation.executed_price == 101.0
    assert confirmation.commission_eur == 4.0
    assert (
        confirmation.broker_order_reference
        == "SYNTHETIC-BROKER-REF"
    )

    # ---------------------------------------------------------
    # 2. Materialize the canonical OPEN TradeOutcome through CLI.
    # ---------------------------------------------------------

    _feed_inputs(
        monkeypatch,
        ["Synthetic OPEN outcome."],
    )

    cli_module.main(
        [
            "--db",
            str(db_path),
            "opportunities",
            "outcome",
            OPPORTUNITY_ID,
        ]
    )

    open_outcome = store.get_latest_trade_outcome(
        OPPORTUNITY_ID
    )
    assert open_outcome is not None
    assert open_outcome.status == TradeOutcomeStatus.OPEN

    original_outcome_id = open_outcome.outcome_id

    # Provenance must survive the complete analytical/execution chain.
    assert open_outcome.opportunity_id == OPPORTUNITY_ID
    assert open_outcome.proposal_id == PROPOSAL_ID
    assert open_outcome.simulation_id == SIMULATION_ID
    assert open_outcome.decision_id == DECISION_ID
    assert open_outcome.execution_plan_id == EXECUTION_PLAN_ID
    assert open_outcome.confirmation_id == confirmation.confirmation_id
    assert open_outcome.snapshot_id == SNAPSHOT_ID
    assert open_outcome.instrument_id == INSTRUMENT_ID

    # Actual entry facts come from the explicit operator confirmation.
    assert open_outcome.entry_quantity == 10.0
    assert open_outcome.entry_price == 101.0
    assert open_outcome.entry_commission_eur == 4.0

    # Entry FX comes from the approved ExecutionPlan.
    assert open_outcome.entry_fx_to_eur == pytest.approx(0.86)

    # ---------------------------------------------------------
    # 3. Fully close the synthetic trade through the real CLI.
    # ---------------------------------------------------------

    exit_datetime = (
        confirmation.created_at
        + timedelta(days=3)
    )

    _feed_inputs(
        monkeypatch,
        [
            exit_datetime.isoformat(),
            "110",
            "",       # keep full-close quantity = 10
            "5",
            "0.87",
            "TARGET_1",
            "Synthetic target exit.",
        ],
    )

    cli_module.main(
        [
            "--db",
            str(db_path),
            "opportunities",
            "outcome-close",
            OPPORTUNITY_ID,
        ]
    )

    closed = store.get_latest_trade_outcome(
        OPPORTUNITY_ID
    )
    assert closed is not None

    # OPEN -> CLOSED updates the same canonical outcome.
    assert closed.outcome_id == original_outcome_id
    assert closed.status == TradeOutcomeStatus.CLOSED

    assert closed.exit_quantity == 10.0
    assert closed.exit_price == 110.0
    assert closed.exit_commission_eur == 5.0
    assert closed.exit_fx_to_eur == pytest.approx(0.87)
    assert closed.exit_reason == TradeExitReason.TARGET_1
    assert closed.holding_days == pytest.approx(3.0)

    # LONG, FX-aware realized P/L:
    #
    # entry EUR = 10 * 101 * 0.86 = 868.60
    # exit  EUR = 10 * 110 * 0.87 = 957.00
    # gross P/L = 88.40
    # net P/L   = 88.40 - 4.00 - 5.00 = 79.40
    #
    expected_entry_notional_eur = 10 * 101 * 0.86
    expected_pnl_eur = (
        (10 * 110 * 0.87)
        - expected_entry_notional_eur
        - 4.0
        - 5.0
    )
    expected_return_pct = (
        expected_pnl_eur
        / expected_entry_notional_eur
        * 100.0
    )

    assert closed.realized_pnl_eur == pytest.approx(
        expected_pnl_eur
    )
    assert closed.realized_return_pct == pytest.approx(
        expected_return_pct
    )

    # ---------------------------------------------------------
    # 4. Direct SQLite/repository verification.
    # ---------------------------------------------------------

    by_plan = store.get_trade_outcome_for_execution_plan(
        EXECUTION_PLAN_ID
    )
    assert by_plan is not None
    assert by_plan.outcome_id == original_outcome_id
    assert by_plan.status == TradeOutcomeStatus.CLOSED

    all_outcomes = store.list_trade_outcomes(
        OPPORTUNITY_ID
    )
    assert len(all_outcomes) == 1

    # Read-only CLI must be able to resolve the persisted CLOSED result.
    cli_module.main(
        [
            "--db",
            str(db_path),
            "opportunities",
            "outcome-show",
            OPPORTUNITY_ID,
        ]
    )

    output = capsys.readouterr().out
    assert "OPERATOR_CONFIRMED" in output
    assert "TradeOutcome created/resolved." in output
    assert "TradeOutcome closed and persisted." in output
    assert "Status:           CLOSED" in output
