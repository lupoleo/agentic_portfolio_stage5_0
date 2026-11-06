from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.cio.models import (
    CioDecision,
    CioDecisionType,
    ExecutionPlan,
    ExecutionPlanStatus,
    FinecoInstrument,
    PortfolioSimulation,
    TradeProposal,
)


class ExecutionPlanBuilder:
    """
    Deterministic builder for broker-ready manual execution plans.

    The builder materializes an already-approved CIO analytical chain:

        TradeProposal
            +
        PortfolioSimulation
            +
        CioDecision(ACCEPT)
            +
        FinecoInstrument
                ↓
        ExecutionPlan
            WAITING_FOR_OPERATOR_CONFIRMATION

    It does NOT:
      - select an instrument;
      - resize a position;
      - change entry/stop/targets;
      - make or override a CIO decision;
      - execute a broker order;
      - mark an order as executed.

    Governance invariant
    --------------------
    The decision, simulation and proposal must refer to the exact same
    proposal/simulation/snapshot chain. The selected Fineco instrument
    must be the instrument persisted in the approved TradeProposal.
    """

    def build(
        self,
        *,
        proposal: TradeProposal,
        simulation: PortfolioSimulation,
        decision: CioDecision,
        instrument: FinecoInstrument,
        execution_notes: str | None = None,
    ) -> ExecutionPlan:

        self._validate_inputs(
            proposal=proposal,
            simulation=simulation,
            decision=decision,
            instrument=instrument,
        )

        now = datetime.now(
            timezone.utc
        )

        return ExecutionPlan(
            execution_plan_id=(
                self._new_execution_plan_id(
                    proposal.ticker,
                    now,
                )
            ),

            created_at=now,

            opportunity_id=(
                proposal.opportunity_id
            ),

            proposal_id=(
                proposal.proposal_id
            ),

            simulation_id=(
                simulation.simulation_id
            ),

            decision_id=(
                decision.decision_id
            ),

            snapshot_id=(
                proposal.snapshot_id
            ),

            broker="Fineco",

            underlying=(
                instrument.reference_underlying
                or instrument.underlying
            ),

            instrument_id=(
                instrument.instrument_id
            ),

            instrument_description=(
                instrument.description
            ),

            broker_symbol=(
                instrument.fineco_symbol
            ),

            market=(
                instrument.market
            ),

            currency=(
                proposal.currency
            ),

            direction=(
                proposal.direction
            ),

            execution_side=(
                proposal.execution_side
            ),

            quantity=(
                proposal.quantity
            ),

            order_type=(
                proposal.entry_type
                .strip()
                .upper()
            ),

            reference_price=(
                proposal.reference_price
            ),

            entry_price=(
                proposal.entry_price
            ),

            stop_price=(
                proposal.stop_price
            ),

            target_1=(
                proposal.target_1
            ),

            target_2=(
                proposal.target_2
            ),

            fx_to_eur=(
                proposal.fx_to_eur
            ),

            gross_exposure_eur=(
                proposal.gross_exposure_eur
            ),

            estimated_capital_required_eur=(
                proposal.estimated_capital_required_eur
            ),

            estimated_max_loss_eur=(
                proposal.estimated_max_loss_eur
            ),

            expected_holding_min_days=(
                proposal.expected_holding_min_days
            ),

            expected_holding_max_days=(
                proposal.expected_holding_max_days
            ),

            status=(
                ExecutionPlanStatus
                .WAITING_FOR_OPERATOR_CONFIRMATION
            ),

            execution_notes=(
                execution_notes
            ),
        )

    # =========================================================
    # Validation
    # =========================================================

    def _validate_inputs(
        self,
        *,
        proposal: TradeProposal,
        simulation: PortfolioSimulation,
        decision: CioDecision,
        instrument: FinecoInstrument,
    ) -> None:

        # -----------------------------------------------------
        # CIO approval
        # -----------------------------------------------------

        if (
            decision.decision
            != CioDecisionType.ACCEPT
        ):
            raise ValueError(
                "ExecutionPlan requires a CIO decision "
                "with decision=ACCEPT"
            )

        if (
            decision.hard_constraints_passed
            is not True
        ):
            raise ValueError(
                "ExecutionPlan requires "
                "hard_constraints_passed=True"
            )

        if (
            decision.critical_evidence_complete
            is not True
        ):
            raise ValueError(
                "ExecutionPlan requires "
                "critical_evidence_complete=True"
            )

        # -----------------------------------------------------
        # Exact decision -> simulation -> proposal chain
        # -----------------------------------------------------

        if (
            decision.proposal_id
            != proposal.proposal_id
        ):
            raise ValueError(
                "CioDecision proposal_id does not match "
                "TradeProposal"
            )

        if (
            decision.simulation_id
            != simulation.simulation_id
        ):
            raise ValueError(
                "CioDecision simulation_id does not match "
                "PortfolioSimulation"
            )

        if (
            simulation.proposal_id
            != proposal.proposal_id
        ):
            raise ValueError(
                "PortfolioSimulation proposal_id does not match "
                "TradeProposal"
            )

        if (
            simulation.snapshot_id
            != proposal.snapshot_id
        ):
            raise ValueError(
                "PortfolioSimulation snapshot_id does not match "
                "TradeProposal"
            )

        # New decisions should carry both IDs. Do not silently accept
        # missing provenance at the execution boundary.
        if not decision.opportunity_id:
            raise ValueError(
                "CioDecision opportunity_id is required "
                "for ExecutionPlan creation"
            )

        if (
            decision.opportunity_id
            != proposal.opportunity_id
        ):
            raise ValueError(
                "CioDecision opportunity_id does not match "
                "TradeProposal"
            )

        if not decision.snapshot_id:
            raise ValueError(
                "CioDecision snapshot_id is required "
                "for ExecutionPlan creation"
            )

        if (
            decision.snapshot_id
            != proposal.snapshot_id
        ):
            raise ValueError(
                "CioDecision snapshot_id does not match "
                "TradeProposal"
            )

        # -----------------------------------------------------
        # Persisted simulation must still be a passing simulation
        # -----------------------------------------------------

        if not simulation.constraints_passed:
            raise ValueError(
                "ExecutionPlan cannot be created from a "
                "PortfolioSimulation that failed constraints"
            )

        # -----------------------------------------------------
        # Exact approved broker instrument
        # -----------------------------------------------------

        if (
            instrument.instrument_id
            != proposal.instrument_id
        ):
            raise ValueError(
                "FinecoInstrument instrument_id does not match "
                "the approved TradeProposal"
            )

        # -----------------------------------------------------
        # Broker-ready trade structure
        # -----------------------------------------------------

        if proposal.execution_side is None:
            raise ValueError(
                "TradeProposal execution_side is required "
                "for ExecutionPlan creation"
            )

        if not proposal.entry_type.strip():
            raise ValueError(
                "TradeProposal entry_type is required "
                "for ExecutionPlan creation"
            )

    # =========================================================
    # IDs
    # =========================================================

    def _new_execution_plan_id(
        self,
        ticker: str,
        now: datetime,
    ) -> str:

        return (
            f"EXEC-"
            f"{ticker.upper()}-"
            f"{now:%Y%m%d-%H%M%S}-"
            f"{uuid4().hex[:6]}"
        )