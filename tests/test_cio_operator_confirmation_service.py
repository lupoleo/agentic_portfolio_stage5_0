from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.cio.models import (
    ExecutionPlanStatus,
    OperatorConfirmation,
    OperatorConfirmationOutcome,
)
from app.cio.operator_confirmation_service import (
    OperatorConfirmationService,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    25,
    22,
    0,
    tzinfo=timezone.utc,
)


def _plan(
    *,
    execution_plan_id: str = "EXEC-SMCI-001",
    status: ExecutionPlanStatus = (
        ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
    ),
    quantity: float = 429,
    underlying: str = "SMCI",
):
    return SimpleNamespace(
        execution_plan_id=execution_plan_id,
        status=status,
        quantity=quantity,
        underlying=underlying,
        model_dump=lambda: {
            "execution_plan_id": execution_plan_id,
            "status": status,
            "quantity": quantity,
            "underlying": underlying,
        },
    )


class _ExecutionPlanStub(SimpleNamespace):
    def model_dump(
        self,
    ):
        return {
            "execution_plan_id":
                self.execution_plan_id,

            "status":
                self.status,

            "quantity":
                self.quantity,

            "underlying":
                self.underlying,
        }

    @classmethod
    def model_validate(
        cls,
        data,
    ):
        return cls(**data)


def _typed_plan(
    *,
    execution_plan_id: str = "EXEC-SMCI-001",
    status: ExecutionPlanStatus = (
        ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
    ),
    quantity: float = 429,
    underlying: str = "SMCI",
):
    return _ExecutionPlanStub(
        execution_plan_id=execution_plan_id,
        status=status,
        quantity=quantity,
        underlying=underlying,
    )


def _store_with_waiting_plan():
    store = Mock(spec=Stage3Store)

    plan = _typed_plan()

    store.get_execution_plan.return_value = plan
    store.get_latest_operator_confirmation_for_execution_plan.return_value = None

    return store, plan


def test_confirm_executed_persists_confirmation_and_updates_plan():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    confirmation = service.confirm_executed(
        plan.execution_plan_id,
        executed_quantity=429,
        executed_price=37.18,
        commission_eur=9.95,
        broker_order_reference="FINECO-123",
        notes="Executed manually.",
    )

    assert isinstance(
        confirmation,
        OperatorConfirmation,
    )

    assert (
        confirmation.outcome
        == OperatorConfirmationOutcome.EXECUTED
    )

    assert confirmation.executed_quantity == 429
    assert confirmation.executed_price == 37.18
    assert confirmation.commission_eur == 9.95
    assert (
        confirmation.broker_order_reference
        == "FINECO-123"
    )

    store.save_operator_confirmation.assert_called_once()

    saved_confirmation = (
        store.save_operator_confirmation.call_args.args[0]
    )

    assert saved_confirmation == confirmation

    store.save_execution_plan.assert_called_once()

    saved_plan = (
        store.save_execution_plan.call_args.args[0]
    )

    assert (
        saved_plan.status
        == ExecutionPlanStatus.OPERATOR_CONFIRMED
    )


def test_confirm_cancelled_persists_confirmation_and_updates_plan():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    confirmation = service.confirm_cancelled(
        plan.execution_plan_id,
        notes="Operator cancelled before execution.",
    )

    assert (
        confirmation.outcome
        == OperatorConfirmationOutcome.CANCELLED
    )

    assert confirmation.executed_quantity is None
    assert confirmation.executed_price is None

    store.save_operator_confirmation.assert_called_once()
    store.save_execution_plan.assert_called_once()

    saved_plan = (
        store.save_execution_plan.call_args.args[0]
    )

    assert (
        saved_plan.status
        == ExecutionPlanStatus.CANCELLED
    )


def test_missing_execution_plan_is_rejected():

    store = Mock(spec=Stage3Store)
    store.get_execution_plan.return_value = None

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="ExecutionPlan not found",
    ):
        service.confirm_executed(
            "EXEC-MISSING",
            executed_quantity=1,
            executed_price=10,
        )


@pytest.mark.parametrize(
    "status",
    [
        ExecutionPlanStatus.OPERATOR_CONFIRMED,
        ExecutionPlanStatus.CANCELLED,
        ExecutionPlanStatus.SUPERSEDED,
    ],
)
def test_non_waiting_execution_plan_is_rejected(
    status,
):

    store = Mock(spec=Stage3Store)

    store.get_execution_plan.return_value = _typed_plan(
        status=status
    )

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="not awaiting operator confirmation",
    ):
        service.confirm_executed(
            "EXEC-SMCI-001",
            executed_quantity=1,
            executed_price=10,
        )


def test_existing_confirmation_blocks_second_confirmation():

    store, plan = _store_with_waiting_plan()

    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        SimpleNamespace(
            confirmation_id="CONF-EXISTING"
        )
    )

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="already has an OperatorConfirmation",
    ):
        service.confirm_executed(
            plan.execution_plan_id,
            executed_quantity=429,
            executed_price=37.18,
        )


def test_executed_requires_quantity():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="executed_quantity is required",
    ):
        service.confirm(
            plan.execution_plan_id,
            outcome=OperatorConfirmationOutcome.EXECUTED,
            executed_quantity=None,
            executed_price=37.18,
        )


def test_executed_requires_price():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="executed_price is required",
    ):
        service.confirm(
            plan.execution_plan_id,
            outcome=OperatorConfirmationOutcome.EXECUTED,
            executed_quantity=429,
            executed_price=None,
        )


def test_executed_quantity_cannot_exceed_authorized_quantity():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="cannot exceed the authorized ExecutionPlan quantity",
    ):
        service.confirm_executed(
            plan.execution_plan_id,
            executed_quantity=430,
            executed_price=37.18,
        )


def test_cancelled_rejects_execution_quantity():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="executed_quantity must be omitted",
    ):
        service.confirm(
            plan.execution_plan_id,
            outcome=OperatorConfirmationOutcome.CANCELLED,
            executed_quantity=1,
        )


def test_cancelled_rejects_execution_price():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="executed_price must be omitted",
    ):
        service.confirm(
            plan.execution_plan_id,
            outcome=OperatorConfirmationOutcome.CANCELLED,
            executed_price=10,
        )


def test_cancelled_rejects_commission():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="commission_eur must be omitted",
    ):
        service.confirm(
            plan.execution_plan_id,
            outcome=OperatorConfirmationOutcome.CANCELLED,
            commission_eur=1,
        )


def test_cancelled_rejects_broker_reference():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    with pytest.raises(
        ValueError,
        match="broker_order_reference must be omitted",
    ):
        service.confirm(
            plan.execution_plan_id,
            outcome=OperatorConfirmationOutcome.CANCELLED,
            broker_order_reference="FINECO-123",
        )


def test_confirmation_id_is_generated_from_underlying():

    store, plan = _store_with_waiting_plan()

    service = OperatorConfirmationService(
        store
    )

    confirmation = service.confirm_executed(
        plan.execution_plan_id,
        executed_quantity=429,
        executed_price=37.18,
    )

    assert confirmation.confirmation_id.startswith(
        "CONF-SMCI-"
    )


def test_audit_record_is_persisted_before_plan_transition():

    store, plan = _store_with_waiting_plan()

    call_order: list[str] = []

    store.save_operator_confirmation.side_effect = (
        lambda _confirmation: call_order.append(
            "confirmation"
        )
    )

    store.save_execution_plan.side_effect = (
        lambda _plan: call_order.append(
            "plan"
        )
    )

    service = OperatorConfirmationService(
        store
    )

    service.confirm_executed(
        plan.execution_plan_id,
        executed_quantity=429,
        executed_price=37.18,
    )

    assert call_order == [
        "confirmation",
        "plan",
    ]
