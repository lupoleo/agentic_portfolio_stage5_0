from __future__ import annotations

from datetime import datetime

from app.cio.models import (
    ExecutionPlanStatus,
    OperatorConfirmationOutcome,
    TradeExitReason,
    TradeOutcome,
    TradeOutcomeStatus,
)
from app.cio.storage import Stage3Store
from app.cio.trade_outcome_builder import TradeOutcomeBuilder
from app.cio.trade_outcome_calculator import TradeOutcomeCalculator


class TradeOutcomeService:
    """
    Orchestration service for TradeOutcome lifecycle.

    Responsibilities
    ----------------
    1. Materialize the canonical OPEN TradeOutcome after an explicitly
       confirmed broker execution.
    2. Close an existing OPEN TradeOutcome using operator-supplied exit
       facts plus deterministic realized-economics calculations.

    The service never executes a broker order and never infers execution.
    """

    def __init__(
        self,
        store: Stage3Store,
    ) -> None:
        self.store = store
        self.builder = TradeOutcomeBuilder()
        self.calculator = TradeOutcomeCalculator()

    def create_open_outcome(
        self,
        opportunity_id: str,
        *,
        notes: str | None = None,
    ) -> TradeOutcome:
        """
        Resolve the latest executed manual broker chain for one opportunity,
        build its canonical OPEN TradeOutcome and persist it.

        If an outcome already exists for the resolved ExecutionPlan, return
        that persisted outcome idempotently.
        """

        opportunity = self.store.get_trade_opportunity(
            opportunity_id
        )

        if opportunity is None:
            raise ValueError(
                "TradeOpportunity not found: "
                f"{opportunity_id}"
            )

        plan = self.store.get_latest_execution_plan(
            opportunity_id
        )

        if plan is None:
            raise ValueError(
                "No ExecutionPlan exists for "
                f"{opportunity_id}"
            )

        if plan.opportunity_id != opportunity_id:
            raise ValueError(
                "Latest ExecutionPlan does not belong "
                "to the supplied TradeOpportunity"
            )

        if (
            plan.status
            != ExecutionPlanStatus.OPERATOR_CONFIRMED
        ):
            raise ValueError(
                "TradeOutcome requires the latest ExecutionPlan "
                "to have status=OPERATOR_CONFIRMED"
            )

        existing = (
            self.store
            .get_trade_outcome_for_execution_plan(
                plan.execution_plan_id
            )
        )

        if existing is not None:
            return existing

        confirmation = (
            self.store
            .get_latest_operator_confirmation_for_execution_plan(
                plan.execution_plan_id
            )
        )

        if confirmation is None:
            raise ValueError(
                "No OperatorConfirmation exists for "
                f"{plan.execution_plan_id}"
            )

        if (
            confirmation.execution_plan_id
            != plan.execution_plan_id
        ):
            raise ValueError(
                "Latest OperatorConfirmation does not belong "
                "to the resolved ExecutionPlan"
            )

        if (
            confirmation.outcome
            != OperatorConfirmationOutcome.EXECUTED
        ):
            raise ValueError(
                "TradeOutcome requires OperatorConfirmation "
                "outcome=EXECUTED"
            )

        outcome = self.builder.build_open(
            plan=plan,
            confirmation=confirmation,
            notes=notes,
        )

        self.store.save_trade_outcome(
            outcome
        )

        return outcome

    def close_outcome(
        self,
        opportunity_id: str,
        *,
        exit_datetime: datetime,
        exit_price: float,
        exit_quantity: float,
        exit_reason: TradeExitReason,
        exit_commission_eur: float = 0.0,
        exit_fx_to_eur: float | None = None,
        notes: str | None = None,
    ) -> TradeOutcome:
        """
        Fully close the latest OPEN TradeOutcome for one opportunity.

        V1 supports full close only.

        Operator-supplied facts:
          - exit_datetime
          - exit_price
          - exit_quantity
          - exit_reason
          - exit_commission_eur
          - exit_fx_to_eur for non-EUR trades

        Deterministically calculated:
          - realized_pnl_eur
          - realized_return_pct
          - holding_days
          - entry_slippage_pct

        The same outcome_id is preserved and persisted as CLOSED.
        """

        opportunity = self.store.get_trade_opportunity(
            opportunity_id
        )

        if opportunity is None:
            raise ValueError(
                "TradeOpportunity not found: "
                f"{opportunity_id}"
            )

        outcome = self.store.get_latest_trade_outcome(
            opportunity_id
        )

        if outcome is None:
            raise ValueError(
                "No TradeOutcome exists for "
                f"{opportunity_id}"
            )

        if outcome.opportunity_id != opportunity_id:
            raise ValueError(
                "Latest TradeOutcome does not belong "
                "to the supplied TradeOpportunity"
            )

        if outcome.status != TradeOutcomeStatus.OPEN:
            raise ValueError(
                "TradeOutcome close requires status=OPEN"
            )

        calculation = self.calculator.calculate_closed(
            outcome=outcome,
            exit_datetime=exit_datetime,
            exit_price=exit_price,
            exit_quantity=exit_quantity,
            exit_commission_eur=exit_commission_eur,
            exit_fx_to_eur=exit_fx_to_eur,
        )

        data = outcome.model_dump()

        data.update(
            {
                "updated_at": exit_datetime,
                "exit_datetime": exit_datetime,
                "exit_quantity": exit_quantity,
                "exit_price": exit_price,
                "exit_commission_eur": exit_commission_eur,
                "exit_fx_to_eur": exit_fx_to_eur,
                "exit_reason": exit_reason,
                "realized_pnl_eur": calculation.realized_pnl_eur,
                "realized_return_pct": calculation.realized_return_pct,
                "holding_days": calculation.holding_days,
                "entry_slippage_pct": calculation.entry_slippage_pct,
                "status": TradeOutcomeStatus.CLOSED,
                "notes": (
                    notes
                    if notes is not None
                    else outcome.notes
                ),
            }
        )

        closed = TradeOutcome.model_validate(
            data
        )

        self.store.save_trade_outcome(
            closed
        )

        return closed
