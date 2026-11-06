from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.cio.models import (
    ExecutionPlanStatus,
    OperatorConfirmation,
    OperatorConfirmationOutcome,
)
from app.cio.storage import Stage3Store


class OperatorConfirmationService:
    """
    Orchestration service for explicit human confirmation of a manual
    broker execution instruction.

    This service is the governance boundary between:

        ExecutionPlan
            what the CIO authorized

        OperatorConfirmation
            what the human operator reports actually happened

    Supported transitions
    ---------------------

        WAITING_FOR_OPERATOR_CONFIRMATION
            + EXECUTED
                -> persist OperatorConfirmation
                -> ExecutionPlan.OPERATOR_CONFIRMED

        WAITING_FOR_OPERATOR_CONFIRMATION
            + CANCELLED
                -> persist OperatorConfirmation
                -> ExecutionPlan.CANCELLED

    The service does NOT:
      - execute an order;
      - talk to Fineco;
      - infer an execution;
      - confirm execution automatically;
      - modify proposal/simulation/decision provenance.
    """

    def __init__(
        self,
        store: Stage3Store,
    ) -> None:
        self.store = store

    def confirm(
        self,
        execution_plan_id: str,
        *,
        outcome: OperatorConfirmationOutcome,
        executed_quantity: float | None = None,
        executed_price: float | None = None,
        commission_eur: float | None = None,
        broker_order_reference: str | None = None,
        notes: str | None = None,
    ) -> OperatorConfirmation:
        """
        Persist one explicit operator confirmation and transition the
        ExecutionPlan lifecycle state accordingly.
        """

        plan = self.store.get_execution_plan(
            execution_plan_id
        )

        if plan is None:
            raise ValueError(
                "ExecutionPlan not found: "
                f"{execution_plan_id}"
            )

        if (
            plan.status
            != ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
        ):
            raise ValueError(
                "ExecutionPlan is not awaiting operator confirmation: "
                f"{plan.status.value}"
            )

        existing = (
            self.store
            .get_latest_operator_confirmation_for_execution_plan(
                execution_plan_id
            )
        )

        if existing is not None:
            raise ValueError(
                "ExecutionPlan already has an OperatorConfirmation: "
                f"{existing.confirmation_id}"
            )

        if outcome == OperatorConfirmationOutcome.EXECUTED:

            if executed_quantity is None:
                raise ValueError(
                    "executed_quantity is required when "
                    "outcome=EXECUTED"
                )

            if executed_price is None:
                raise ValueError(
                    "executed_price is required when "
                    "outcome=EXECUTED"
                )

            if executed_quantity > plan.quantity:
                raise ValueError(
                    "executed_quantity cannot exceed the authorized "
                    f"ExecutionPlan quantity ({plan.quantity:g})"
                )

        elif outcome == OperatorConfirmationOutcome.CANCELLED:

            if executed_quantity is not None:
                raise ValueError(
                    "executed_quantity must be omitted when "
                    "outcome=CANCELLED"
                )

            if executed_price is not None:
                raise ValueError(
                    "executed_price must be omitted when "
                    "outcome=CANCELLED"
                )

            if commission_eur is not None:
                raise ValueError(
                    "commission_eur must be omitted when "
                    "outcome=CANCELLED"
                )

            if broker_order_reference is not None:
                raise ValueError(
                    "broker_order_reference must be omitted when "
                    "outcome=CANCELLED"
                )

        now = datetime.now(
            timezone.utc
        )

        confirmation = OperatorConfirmation(
            confirmation_id=self._new_confirmation_id(
                plan.underlying,
                now,
            ),
            execution_plan_id=execution_plan_id,
            created_at=now,
            outcome=outcome,
            executed_quantity=executed_quantity,
            executed_price=executed_price,
            commission_eur=commission_eur,
            broker_order_reference=broker_order_reference,
            notes=notes,
        )

        # Persist audit record first.
        self.store.save_operator_confirmation(
            confirmation
        )

        # Then transition the ExecutionPlan lifecycle.
        data = plan.model_dump()

        if outcome == OperatorConfirmationOutcome.EXECUTED:
            data["status"] = ExecutionPlanStatus.OPERATOR_CONFIRMED

        elif outcome == OperatorConfirmationOutcome.CANCELLED:
            data["status"] = ExecutionPlanStatus.CANCELLED

        updated_plan = type(plan).model_validate(
            data
        )

        self.store.save_execution_plan(
            updated_plan
        )

        return confirmation

    def confirm_executed(
        self,
        execution_plan_id: str,
        *,
        executed_quantity: float,
        executed_price: float,
        commission_eur: float | None = None,
        broker_order_reference: str | None = None,
        notes: str | None = None,
    ) -> OperatorConfirmation:
        """
        Convenience wrapper for outcome=EXECUTED.
        """

        return self.confirm(
            execution_plan_id,
            outcome=OperatorConfirmationOutcome.EXECUTED,
            executed_quantity=executed_quantity,
            executed_price=executed_price,
            commission_eur=commission_eur,
            broker_order_reference=broker_order_reference,
            notes=notes,
        )

    def confirm_cancelled(
        self,
        execution_plan_id: str,
        *,
        notes: str | None = None,
    ) -> OperatorConfirmation:
        """
        Convenience wrapper for outcome=CANCELLED.
        """

        return self.confirm(
            execution_plan_id,
            outcome=OperatorConfirmationOutcome.CANCELLED,
            notes=notes,
        )

    def _new_confirmation_id(
        self,
        underlying: str,
        now: datetime,
    ) -> str:

        return (
            f"CONF-"
            f"{underlying.upper()}-"
            f"{now:%Y%m%d-%H%M%S}-"
            f"{uuid4().hex[:6]}"
        )
