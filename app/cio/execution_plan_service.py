from __future__ import annotations

from app.cio.execution_plan_builder import (
    ExecutionPlanBuilder,
)
from app.cio.models import (
    CioDecisionType,
    ExecutionPlan,
)
from app.cio.storage import Stage3Store


class ExecutionPlanService:
    """
    Stage 3 orchestration service for broker-ready manual ExecutionPlans.

    Canonical resolution path:

        TradeOpportunity
                ↓
        latest persisted CioDecision
                ↓
        decision.proposal_id
                ↓
        TradeProposal
                ↓
        decision.simulation_id
                ↓
        PortfolioSimulation
                ↓
        proposal.instrument_id
                ↓
        FinecoInstrument
                ↓
        ExecutionPlanBuilder
                ↓
        persist ExecutionPlan
                ↓
        WAITING_FOR_OPERATOR_CONFIRMATION

    The latest CioDecision is the canonical anchor. The service does not
    independently pick "latest proposal" and "latest simulation", because
    doing so could accidentally combine records from different analytical
    revisions.

    Idempotency is metadata-aware:
    an existing ExecutionPlan is reused only when it refers to the same CIO
    decision AND the denormalized broker metadata still matches the current
    FinecoInstrument cache.

    This allows controlled rematerialization when broker metadata changes
    (for example when a previously UNKNOWN Fineco symbol is later resolved)
    while preserving older ExecutionPlans as historical audit records.

    The service does NOT:
      - discover an opportunity;
      - select or rank broker instruments;
      - perform position sizing;
      - create or modify a TradeProposal;
      - perform portfolio simulation;
      - make or override a CIO decision;
      - execute a broker order;
      - confirm broker execution.
    """

    def __init__(
        self,
        store: Stage3Store,
    ) -> None:

        self.store = store
        self.builder = ExecutionPlanBuilder()

    def create_execution_plan(
        self,
        opportunity_id: str,
        *,
        execution_notes: str | None = None,
    ) -> ExecutionPlan:
        """
        Build and persist the broker-ready manual execution plan for the
        latest CIO-approved analytical chain of one opportunity.

        Idempotency
        -----------
        The latest persisted ExecutionPlan for the approved proposal is
        reused only when:

          - it points to the same CIO decision; and
          - its denormalized broker metadata still matches the current
            FinecoInstrument cache.

        If broker metadata has changed, a new ExecutionPlan is materialized
        so the previous plan remains an immutable historical record.
        """

        # -----------------------------------------------------
        # Opportunity
        # -----------------------------------------------------

        opportunity = (
            self.store.get_trade_opportunity(
                opportunity_id
            )
        )

        if opportunity is None:
            raise ValueError(
                "TradeOpportunity not found: "
                f"{opportunity_id}"
            )

        # -----------------------------------------------------
        # Latest CIO decision is the canonical anchor.
        # -----------------------------------------------------

        decision = (
            self.store.get_latest_cio_decision(
                opportunity_id
            )
        )

        if decision is None:
            raise ValueError(
                "No CioDecision exists for "
                f"{opportunity_id}"
            )

        if (
            decision.opportunity_id
            != opportunity_id
        ):
            raise ValueError(
                "Latest CioDecision does not belong "
                "to the supplied TradeOpportunity"
            )

        if (
            decision.decision
            != CioDecisionType.ACCEPT
        ):
            raise ValueError(
                "ExecutionPlan requires the latest CIO "
                "decision to be ACCEPT"
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
        # Proposal explicitly authorized by that decision.
        # -----------------------------------------------------

        proposal = (
            self.store.get_trade_proposal(
                decision.proposal_id
            )
        )

        if proposal is None:
            raise ValueError(
                "TradeProposal referenced by CioDecision "
                "was not found: "
                f"{decision.proposal_id}"
            )

        if (
            proposal.opportunity_id
            != opportunity_id
        ):
            raise ValueError(
                "CioDecision TradeProposal does not belong "
                "to the supplied TradeOpportunity"
            )

        # -----------------------------------------------------
        # Exact simulation explicitly authorized by decision.
        # -----------------------------------------------------

        simulation = (
            self.store.get_portfolio_simulation(
                decision.simulation_id
            )
        )

        if simulation is None:
            raise ValueError(
                "PortfolioSimulation referenced by "
                "CioDecision was not found: "
                f"{decision.simulation_id}"
            )

        # -----------------------------------------------------
        # Exact broker instrument persisted in approved proposal.
        # -----------------------------------------------------

        instrument = (
            self.store.get_fineco_instrument(
                proposal.instrument_id
            )
        )

        if instrument is None:
            raise ValueError(
                "Fineco instrument referenced by approved "
                "TradeProposal is no longer available in "
                "the local cache: "
                f"{proposal.instrument_id}"
            )

        # -----------------------------------------------------
        # Metadata-aware idempotency guard.
        # -----------------------------------------------------

        existing = (
            self.store
            .get_latest_execution_plan_for_proposal(
                proposal.proposal_id
            )
        )

        if (
            existing is not None
            and existing.decision_id
            == decision.decision_id
            and self._broker_metadata_matches(
                existing=existing,
                instrument=instrument,
            )
        ):
            return existing

        # -----------------------------------------------------
        # Build canonical ExecutionPlan.
        #
        # ExecutionPlanBuilder performs the strict provenance
        # validation across proposal/simulation/decision/instrument.
        # -----------------------------------------------------

        plan = (
            self.builder.build(
                proposal=proposal,
                simulation=simulation,
                decision=decision,
                instrument=instrument,
                execution_notes=execution_notes,
            )
        )

        # -----------------------------------------------------
        # Persist.
        # -----------------------------------------------------

        self.store.save_execution_plan(
            plan
        )

        return plan

    # =========================================================
    # Idempotency helpers
    # =========================================================

    def _broker_metadata_matches(
        self,
        *,
        existing: ExecutionPlan,
        instrument,
    ) -> bool:
        """
        Return True when the denormalized broker metadata embedded in an
        existing ExecutionPlan still matches the current FinecoInstrument.

        These fields are operational metadata only; a change does not alter
        the approved trade thesis, sizing, simulation or CIO decision.
        """

        current_underlying = (
            instrument.reference_underlying
            or instrument.underlying
        )

        return (
            existing.instrument_id
            == instrument.instrument_id

            and existing.underlying
            == current_underlying

            and existing.instrument_description
            == instrument.description

            and existing.broker_symbol
            == instrument.fineco_symbol

            and existing.market
            == instrument.market
        )
