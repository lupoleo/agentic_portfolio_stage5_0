from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.cio.models import (
    Currency,
    Direction,
    ExecutionPlanStatus,
    ExecutionSide,
    OperatorConfirmationOutcome,
    TradeExitReason,
    TradeOutcome,
    TradeOutcomeStatus,
)
from app.cio.storage import Stage3Store
from app.cio.trade_outcome_calculator import (
    TradeOutcomeCalculation,
)
from app.cio.trade_outcome_service import (
    TradeOutcomeService,
)


NOW = datetime(
    2026,
    8,
    27,
    21,
    30,
    tzinfo=timezone.utc,
)

OPPORTUNITY_ID = "OPP-OUTCOME-SERVICE"
EXECUTION_PLAN_ID = "EXEC-OUTCOME-SERVICE"
CONFIRMATION_ID = "CONF-OUTCOME-SERVICE"


def _opportunity():
    return SimpleNamespace(
        opportunity_id=OPPORTUNITY_ID,
    )


def _plan(
    *,
    opportunity_id: str = OPPORTUNITY_ID,
    status: ExecutionPlanStatus = (
        ExecutionPlanStatus.OPERATOR_CONFIRMED
    ),
):
    return SimpleNamespace(
        execution_plan_id=EXECUTION_PLAN_ID,
        opportunity_id=opportunity_id,
        status=status,
    )


def _confirmation(
    *,
    execution_plan_id: str = EXECUTION_PLAN_ID,
    outcome: OperatorConfirmationOutcome = (
        OperatorConfirmationOutcome.EXECUTED
    ),
):
    return SimpleNamespace(
        confirmation_id=CONFIRMATION_ID,
        execution_plan_id=execution_plan_id,
        outcome=outcome,
    )


def _open_outcome(
    *,
    opportunity_id: str = OPPORTUNITY_ID,
    status: TradeOutcomeStatus = TradeOutcomeStatus.OPEN,
) -> TradeOutcome:

    data = dict(
        outcome_id="OUT-OUTCOME-SERVICE",
        created_at=NOW,
        updated_at=NOW,
        opportunity_id=opportunity_id,
        proposal_id="PROP-OUTCOME-SERVICE",
        simulation_id="SIM-OUTCOME-SERVICE",
        decision_id="DEC-OUTCOME-SERVICE",
        execution_plan_id=EXECUTION_PLAN_ID,
        confirmation_id=CONFIRMATION_ID,
        snapshot_id="SNAP-OUTCOME-SERVICE",
        ticker="TEST",
        instrument_id="FIN-OUTCOME-SERVICE",
        direction=Direction.LONG,
        execution_side=ExecutionSide.BUY,
        currency=Currency.USD,
        entry_datetime=NOW,
        entry_quantity=10,
        entry_price=100.0,
        entry_commission_eur=5.0,
        entry_fx_to_eur=0.86,
        planned_reference_price=99.0,
        planned_stop_price=95.0,
        planned_target_1=110.0,
        planned_target_2=115.0,
        planned_max_loss_eur=45.0,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        status=status,
        notes="Original notes.",
    )

    if status == TradeOutcomeStatus.CLOSED:
        data.update(
            {
                "updated_at": NOW + timedelta(days=2),
                "exit_datetime": NOW + timedelta(days=2),
                "exit_quantity": 10,
                "exit_price": 110.0,
                "exit_commission_eur": 5.0,
                "exit_fx_to_eur": 0.87,
                "exit_reason": TradeExitReason.TARGET_1,
                "realized_pnl_eur": 87.0,
                "realized_return_pct": 10.116279,
                "holding_days": 2.0,
                "entry_slippage_pct": 1.010101,
            }
        )

    return TradeOutcome.model_validate(
        data
    )


def _outcome_stub():
    return SimpleNamespace(
        outcome_id="OUT-OUTCOME-SERVICE",
        execution_plan_id=EXECUTION_PLAN_ID,
    )


def _store_with_valid_open_chain() -> Mock:

    store = Mock(
        spec=Stage3Store
    )

    store.get_trade_opportunity.return_value = (
        _opportunity()
    )

    store.get_latest_execution_plan.return_value = (
        _plan()
    )

    store.get_trade_outcome_for_execution_plan.return_value = (
        None
    )

    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        _confirmation()
    )

    return store


# =============================================================
# Existing create_open_outcome tests
# =============================================================

def test_create_open_outcome_resolves_builds_and_persists():

    store = _store_with_valid_open_chain()
    service = TradeOutcomeService(store)

    outcome = _outcome_stub()

    service.builder = Mock()
    service.builder.build_open.return_value = outcome

    result = service.create_open_outcome(
        OPPORTUNITY_ID,
        notes="Outcome service test.",
    )

    assert result is outcome
    store.save_trade_outcome.assert_called_once_with(
        outcome
    )


def test_existing_outcome_is_returned_idempotently():

    store = _store_with_valid_open_chain()
    existing = _outcome_stub()

    store.get_trade_outcome_for_execution_plan.return_value = (
        existing
    )

    service = TradeOutcomeService(store)
    service.builder = Mock()

    result = service.create_open_outcome(
        OPPORTUNITY_ID
    )

    assert result is existing
    service.builder.build_open.assert_not_called()
    store.save_trade_outcome.assert_not_called()


def test_missing_opportunity_is_rejected():

    store = _store_with_valid_open_chain()
    store.get_trade_opportunity.return_value = None

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="TradeOpportunity not found",
    ):
        service.create_open_outcome(
            OPPORTUNITY_ID
        )


def test_missing_execution_plan_is_rejected():

    store = _store_with_valid_open_chain()
    store.get_latest_execution_plan.return_value = None

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="No ExecutionPlan exists",
    ):
        service.create_open_outcome(
            OPPORTUNITY_ID
        )


def test_execution_plan_must_belong_to_opportunity():

    store = _store_with_valid_open_chain()
    store.get_latest_execution_plan.return_value = (
        _plan(
            opportunity_id="OPP-OTHER"
        )
    )

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):
        service.create_open_outcome(
            OPPORTUNITY_ID
        )


@pytest.mark.parametrize(
    "status",
    [
        ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION,
        ExecutionPlanStatus.CANCELLED,
        ExecutionPlanStatus.SUPERSEDED,
    ],
)
def test_execution_plan_must_be_operator_confirmed(
    status,
):

    store = _store_with_valid_open_chain()
    store.get_latest_execution_plan.return_value = (
        _plan(status=status)
    )

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="status=OPERATOR_CONFIRMED",
    ):
        service.create_open_outcome(
            OPPORTUNITY_ID
        )


def test_missing_operator_confirmation_is_rejected():

    store = _store_with_valid_open_chain()
    store.get_latest_operator_confirmation_for_execution_plan.return_value = None

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="No OperatorConfirmation exists",
    ):
        service.create_open_outcome(
            OPPORTUNITY_ID
        )


def test_operator_confirmation_must_belong_to_execution_plan():

    store = _store_with_valid_open_chain()
    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        _confirmation(
            execution_plan_id="EXEC-OTHER"
        )
    )

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):
        service.create_open_outcome(
            OPPORTUNITY_ID
        )


def test_operator_confirmation_must_be_executed():

    store = _store_with_valid_open_chain()
    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        _confirmation(
            outcome=OperatorConfirmationOutcome.CANCELLED
        )
    )

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="outcome=EXECUTED",
    ):
        service.create_open_outcome(
            OPPORTUNITY_ID
        )


# =============================================================
# close_outcome tests
# =============================================================

def _store_with_open_outcome() -> Mock:

    store = Mock(
        spec=Stage3Store
    )

    store.get_trade_opportunity.return_value = (
        _opportunity()
    )

    store.get_latest_trade_outcome.return_value = (
        _open_outcome()
    )

    return store


def test_close_outcome_calculates_builds_closed_state_and_persists():

    store = _store_with_open_outcome()
    service = TradeOutcomeService(store)

    service.calculator = Mock()
    service.calculator.calculate_closed.return_value = (
        TradeOutcomeCalculation(
            realized_pnl_eur=87.0,
            realized_return_pct=10.116279,
            holding_days=2.0,
            entry_slippage_pct=1.010101,
        )
    )

    exit_datetime = NOW + timedelta(days=2)

    result = service.close_outcome(
        OPPORTUNITY_ID,
        exit_datetime=exit_datetime,
        exit_price=110.0,
        exit_quantity=10,
        exit_reason=TradeExitReason.TARGET_1,
        exit_commission_eur=5.0,
        exit_fx_to_eur=0.87,
        notes="Target reached.",
    )

    assert result.outcome_id == "OUT-OUTCOME-SERVICE"
    assert result.status == TradeOutcomeStatus.CLOSED
    assert result.updated_at == exit_datetime
    assert result.exit_datetime == exit_datetime
    assert result.exit_quantity == 10
    assert result.exit_price == 110.0
    assert result.exit_commission_eur == 5.0
    assert result.exit_fx_to_eur == 0.87
    assert result.exit_reason == TradeExitReason.TARGET_1
    assert result.realized_pnl_eur == 87.0
    assert result.realized_return_pct == 10.116279
    assert result.holding_days == 2.0
    assert result.entry_slippage_pct == 1.010101
    assert result.notes == "Target reached."

    store.save_trade_outcome.assert_called_once_with(
        result
    )


def test_close_outcome_passes_exact_exit_facts_to_calculator():

    store = _store_with_open_outcome()
    outcome = store.get_latest_trade_outcome.return_value

    service = TradeOutcomeService(store)
    service.calculator = Mock()
    service.calculator.calculate_closed.return_value = (
        TradeOutcomeCalculation(
            realized_pnl_eur=1.0,
            realized_return_pct=1.0,
            holding_days=1.0,
            entry_slippage_pct=None,
        )
    )

    exit_datetime = NOW + timedelta(days=1)

    service.close_outcome(
        OPPORTUNITY_ID,
        exit_datetime=exit_datetime,
        exit_price=105.0,
        exit_quantity=10,
        exit_reason=TradeExitReason.MANUAL,
        exit_commission_eur=3.0,
        exit_fx_to_eur=0.88,
    )

    service.calculator.calculate_closed.assert_called_once_with(
        outcome=outcome,
        exit_datetime=exit_datetime,
        exit_price=105.0,
        exit_quantity=10,
        exit_commission_eur=3.0,
        exit_fx_to_eur=0.88,
    )


def test_close_outcome_preserves_existing_notes_when_none_supplied():

    store = _store_with_open_outcome()
    service = TradeOutcomeService(store)

    service.calculator = Mock()
    service.calculator.calculate_closed.return_value = (
        TradeOutcomeCalculation(
            realized_pnl_eur=1.0,
            realized_return_pct=1.0,
            holding_days=1.0,
            entry_slippage_pct=None,
        )
    )

    result = service.close_outcome(
        OPPORTUNITY_ID,
        exit_datetime=NOW + timedelta(days=1),
        exit_price=105.0,
        exit_quantity=10,
        exit_reason=TradeExitReason.MANUAL,
        exit_fx_to_eur=0.86,
    )

    assert result.notes == "Original notes."


def test_close_outcome_missing_opportunity_is_rejected():

    store = _store_with_open_outcome()
    store.get_trade_opportunity.return_value = None

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="TradeOpportunity not found",
    ):
        service.close_outcome(
            OPPORTUNITY_ID,
            exit_datetime=NOW + timedelta(days=1),
            exit_price=105.0,
            exit_quantity=10,
            exit_reason=TradeExitReason.MANUAL,
            exit_fx_to_eur=0.86,
        )


def test_close_outcome_missing_outcome_is_rejected():

    store = _store_with_open_outcome()
    store.get_latest_trade_outcome.return_value = None

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="No TradeOutcome exists",
    ):
        service.close_outcome(
            OPPORTUNITY_ID,
            exit_datetime=NOW + timedelta(days=1),
            exit_price=105.0,
            exit_quantity=10,
            exit_reason=TradeExitReason.MANUAL,
            exit_fx_to_eur=0.86,
        )


def test_close_outcome_must_belong_to_opportunity():

    store = _store_with_open_outcome()
    store.get_latest_trade_outcome.return_value = (
        _open_outcome(
            opportunity_id="OPP-OTHER"
        )
    )

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):
        service.close_outcome(
            OPPORTUNITY_ID,
            exit_datetime=NOW + timedelta(days=1),
            exit_price=105.0,
            exit_quantity=10,
            exit_reason=TradeExitReason.MANUAL,
            exit_fx_to_eur=0.86,
        )


def test_close_outcome_requires_open_status():

    store = _store_with_open_outcome()
    store.get_latest_trade_outcome.return_value = (
        _open_outcome(
            status=TradeOutcomeStatus.CLOSED
        )
    )

    service = TradeOutcomeService(store)

    with pytest.raises(
        ValueError,
        match="status=OPEN",
    ):
        service.close_outcome(
            OPPORTUNITY_ID,
            exit_datetime=NOW + timedelta(days=3),
            exit_price=111.0,
            exit_quantity=10,
            exit_reason=TradeExitReason.MANUAL,
            exit_fx_to_eur=0.88,
        )


def test_close_outcome_does_not_persist_if_calculator_rejects():

    store = _store_with_open_outcome()
    service = TradeOutcomeService(store)

    service.calculator = Mock()
    service.calculator.calculate_closed.side_effect = (
        ValueError("Full-close V1 requires exit_quantity to equal entry_quantity")
    )

    with pytest.raises(
        ValueError,
        match="Full-close V1",
    ):
        service.close_outcome(
            OPPORTUNITY_ID,
            exit_datetime=NOW + timedelta(days=1),
            exit_price=105.0,
            exit_quantity=5,
            exit_reason=TradeExitReason.MANUAL,
            exit_fx_to_eur=0.86,
        )

    store.save_trade_outcome.assert_not_called()
