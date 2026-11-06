from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.cio.models import (
    ExecutionPlan,
    ExecutionPlanStatus,
    OperatorConfirmation,
    OperatorConfirmationOutcome,
    TradeOutcome,
    TradeOutcomeStatus,
)


class TradeOutcomeBuilder:
    """
    Deterministic builder for the OPEN realized lifecycle record created
    after an operator-confirmed broker execution.

    Canonical input chain:

        ExecutionPlan(OPERATOR_CONFIRMED)
                +
        OperatorConfirmation(EXECUTED)
                ↓
        TradeOutcome(OPEN)

    The builder copies:
      - analytical/execution provenance from ExecutionPlan;
      - actual broker entry facts from OperatorConfirmation;
      - the approved stop/targets/risk/holding snapshot from ExecutionPlan.

    It does NOT:
      - execute a broker order;
      - infer an execution;
      - close the trade;
      - calculate realized P/L;
      - calculate realized return;
      - calculate holding period;
      - calculate execution-quality analytics such as slippage.
    """

    def build_open(
        self,
        *,
        plan: ExecutionPlan,
        confirmation: OperatorConfirmation,
        notes: str | None = None,
    ) -> TradeOutcome:
        """
        Build a new OPEN TradeOutcome from one executed manual broker plan.
        """

        self._validate_inputs(
            plan=plan,
            confirmation=confirmation,
        )

        now = datetime.now(
            timezone.utc
        )

        assert confirmation.executed_quantity is not None
        assert confirmation.executed_price is not None

        return TradeOutcome(
            outcome_id=(
                self._new_outcome_id(
                    plan.underlying,
                    now,
                )
            ),

            created_at=now,
            updated_at=now,

            opportunity_id=plan.opportunity_id,
            proposal_id=plan.proposal_id,
            simulation_id=plan.simulation_id,
            decision_id=plan.decision_id,
            execution_plan_id=plan.execution_plan_id,
            confirmation_id=confirmation.confirmation_id,
            snapshot_id=plan.snapshot_id,

            ticker=plan.underlying,
            instrument_id=plan.instrument_id,
            direction=plan.direction,
            execution_side=plan.execution_side,
            currency=plan.currency,

            entry_datetime=confirmation.created_at,
            entry_quantity=confirmation.executed_quantity,
            entry_price=confirmation.executed_price,
            entry_commission_eur=confirmation.commission_eur,

            # Preserve the FX used by the approved ExecutionPlan so a
            # later non-EUR close can calculate realized EUR economics
            # using independent entry and exit FX.
            entry_fx_to_eur=plan.fx_to_eur,

            planned_reference_price=plan.reference_price,
            planned_stop_price=plan.stop_price,
            planned_target_1=plan.target_1,
            planned_target_2=plan.target_2,
            planned_max_loss_eur=plan.estimated_max_loss_eur,

            expected_holding_min_days=(
                plan.expected_holding_min_days
            ),
            expected_holding_max_days=(
                plan.expected_holding_max_days
            ),

            exit_datetime=None,
            exit_quantity=None,
            exit_price=None,
            exit_commission_eur=None,
            exit_reason=None,

            realized_pnl_eur=None,
            realized_return_pct=None,
            holding_days=None,

            # Derived execution analytics belong to a later calculator.
            entry_slippage_pct=None,

            status=TradeOutcomeStatus.OPEN,
            notes=notes,
        )

    # =========================================================
    # Validation
    # =========================================================

    def _validate_inputs(
        self,
        *,
        plan: ExecutionPlan,
        confirmation: OperatorConfirmation,
    ) -> None:

        if (
            plan.status
            != ExecutionPlanStatus.OPERATOR_CONFIRMED
        ):
            raise ValueError(
                "TradeOutcome requires ExecutionPlan "
                "status=OPERATOR_CONFIRMED"
            )

        if (
            confirmation.outcome
            != OperatorConfirmationOutcome.EXECUTED
        ):
            raise ValueError(
                "TradeOutcome requires OperatorConfirmation "
                "outcome=EXECUTED"
            )

        if (
            confirmation.execution_plan_id
            != plan.execution_plan_id
        ):
            raise ValueError(
                "OperatorConfirmation execution_plan_id does not "
                "match ExecutionPlan"
            )

        if confirmation.executed_quantity is None:
            raise ValueError(
                "executed_quantity is required for TradeOutcome"
            )

        if confirmation.executed_price is None:
            raise ValueError(
                "executed_price is required for TradeOutcome"
            )

        if (
            confirmation.executed_quantity
            > plan.quantity
        ):
            raise ValueError(
                "executed_quantity cannot exceed "
                "ExecutionPlan quantity"
            )

    # =========================================================
    # IDs
    # =========================================================

    def _new_outcome_id(
        self,
        ticker: str,
        now: datetime,
    ) -> str:

        return (
            f"OUT-"
            f"{ticker.upper()}-"
            f"{now:%Y%m%d-%H%M%S}-"
            f"{uuid4().hex[:6]}"
        )
