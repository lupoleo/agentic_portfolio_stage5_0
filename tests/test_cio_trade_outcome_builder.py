from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.cio.models import (
    Currency,
    Direction,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionSide,
    OperatorConfirmation,
    OperatorConfirmationOutcome,
    TradeOutcomeStatus,
)
from app.cio.trade_outcome_builder import (
    TradeOutcomeBuilder,
)


NOW = datetime(
    2026,
    8,
    27,
    20,
    30,
    tzinfo=timezone.utc,
)


def _plan(
    *,
    status: ExecutionPlanStatus = (
        ExecutionPlanStatus.OPERATOR_CONFIRMED
    ),
    quantity: float = 10,
) -> ExecutionPlan:

    return ExecutionPlan(
        execution_plan_id="EXEC-TEST-001",
        created_at=NOW,
        opportunity_id="OPP-TEST-001",
        proposal_id="PROP-TEST-001",
        simulation_id="SIM-TEST-001",
        decision_id="DEC-TEST-001",
        snapshot_id="SNAP-TEST-001",
        broker="Fineco",
        underlying="TEST",
        instrument_id="FIN-TEST-001",
        instrument_description="Test Ordinary NASDAQ",
        broker_symbol="TEST",
        market="NASDAQ",
        currency=Currency.USD,
        direction=Direction.LONG,
        execution_side=ExecutionSide.BUY,
        quantity=quantity,
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
        status=status,
        execution_notes="Builder test.",
    )


def _confirmation(
    *,
    outcome: OperatorConfirmationOutcome = (
        OperatorConfirmationOutcome.EXECUTED
    ),
    execution_plan_id: str = "EXEC-TEST-001",
    executed_quantity: float | None = 10,
    executed_price: float | None = 101.25,
) -> OperatorConfirmation:

    if outcome == OperatorConfirmationOutcome.CANCELLED:
        executed_quantity = None
        executed_price = None

    return OperatorConfirmation(
        confirmation_id="CONF-TEST-001",
        execution_plan_id=execution_plan_id,
        created_at=NOW,
        outcome=outcome,
        executed_quantity=executed_quantity,
        executed_price=executed_price,
        commission_eur=(
            4.95
            if outcome == OperatorConfirmationOutcome.EXECUTED
            else None
        ),
        broker_order_reference=(
            "BROKER-001"
            if outcome == OperatorConfirmationOutcome.EXECUTED
            else None
        ),
        notes="Builder test confirmation.",
    )


def test_build_open_outcome_from_confirmed_execution():

    plan = _plan()
    confirmation = _confirmation()

    outcome = TradeOutcomeBuilder().build_open(
        plan=plan,
        confirmation=confirmation,
        notes="OPEN outcome.",
    )

    assert outcome.status == TradeOutcomeStatus.OPEN

    assert outcome.opportunity_id == plan.opportunity_id
    assert outcome.proposal_id == plan.proposal_id
    assert outcome.simulation_id == plan.simulation_id
    assert outcome.decision_id == plan.decision_id
    assert (
        outcome.execution_plan_id
        == plan.execution_plan_id
    )
    assert (
        outcome.confirmation_id
        == confirmation.confirmation_id
    )
    assert outcome.snapshot_id == plan.snapshot_id

    assert outcome.ticker == "TEST"
    assert outcome.instrument_id == plan.instrument_id
    assert outcome.direction == Direction.LONG
    assert outcome.execution_side == ExecutionSide.BUY
    assert outcome.currency == Currency.USD

    assert outcome.entry_datetime == confirmation.created_at
    assert outcome.entry_quantity == 10
    assert outcome.entry_price == 101.25
    assert outcome.entry_commission_eur == 4.95

    assert outcome.planned_reference_price == 100.0
    assert outcome.planned_stop_price == 95.0
    assert outcome.planned_target_1 == 110.0
    assert outcome.planned_target_2 == 115.0
    assert outcome.planned_max_loss_eur == 43.0

    assert outcome.expected_holding_min_days == 2
    assert outcome.expected_holding_max_days == 5

    assert outcome.exit_datetime is None
    assert outcome.realized_pnl_eur is None
    assert outcome.entry_slippage_pct is None
    assert outcome.notes == "OPEN outcome."


def test_outcome_id_is_generated_from_underlying():

    outcome = TradeOutcomeBuilder().build_open(
        plan=_plan(),
        confirmation=_confirmation(),
    )

    assert outcome.outcome_id.startswith(
        "OUT-TEST-"
    )


def test_builder_supports_short_execution():

    plan_data = _plan().model_dump()
    plan_data["direction"] = Direction.SHORT
    plan_data["execution_side"] = ExecutionSide.SELL_SHORT

    plan = ExecutionPlan.model_validate(
        plan_data
    )

    outcome = TradeOutcomeBuilder().build_open(
        plan=plan,
        confirmation=_confirmation(),
    )

    assert outcome.direction == Direction.SHORT
    assert (
        outcome.execution_side
        == ExecutionSide.SELL_SHORT
    )


def test_partial_confirmed_quantity_is_copied_as_actual_entry_quantity():

    plan = _plan(
        quantity=10
    )

    confirmation = _confirmation(
        executed_quantity=7
    )

    outcome = TradeOutcomeBuilder().build_open(
        plan=plan,
        confirmation=confirmation,
    )

    assert outcome.entry_quantity == 7


@pytest.mark.parametrize(
    "status",
    [
        ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION,
        ExecutionPlanStatus.CANCELLED,
        ExecutionPlanStatus.SUPERSEDED,
    ],
)
def test_builder_rejects_non_confirmed_execution_plan(
    status,
):

    with pytest.raises(
        ValueError,
        match="status=OPERATOR_CONFIRMED",
    ):
        TradeOutcomeBuilder().build_open(
            plan=_plan(
                status=status
            ),
            confirmation=_confirmation(),
        )


def test_builder_rejects_cancelled_confirmation():

    with pytest.raises(
        ValueError,
        match="outcome=EXECUTED",
    ):
        TradeOutcomeBuilder().build_open(
            plan=_plan(),
            confirmation=_confirmation(
                outcome=OperatorConfirmationOutcome.CANCELLED
            ),
        )


def test_builder_rejects_confirmation_for_different_plan():

    with pytest.raises(
        ValueError,
        match="execution_plan_id does not match",
    ):
        TradeOutcomeBuilder().build_open(
            plan=_plan(),
            confirmation=_confirmation(
                execution_plan_id="EXEC-OTHER"
            ),
        )


def test_builder_rejects_quantity_above_authorized_plan():

    with pytest.raises(
        ValueError,
        match="cannot exceed",
    ):
        TradeOutcomeBuilder().build_open(
            plan=_plan(
                quantity=10
            ),
            confirmation=_confirmation(
                executed_quantity=11
            ),
        )
