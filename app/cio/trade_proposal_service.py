from __future__ import annotations

from app.cio.models import (
    OpportunityStatus,
    TradeProposal,
)
from app.cio.storage import Stage3Store
from app.cio.trade_proposal_builder import (
    TradeProposalBuilder,
)


class TradeProposalService:
    """
    Stage 3.2 orchestration service for preliminary Trade Proposals.

    Pipeline:

        persisted TradeOpportunity
            POSITION_SIZED
                ↓
        latest persisted PositionSizingResult
            constraints_passed=True
                ↓
        TradeProposalBuilder
                ↓
        persist TradeProposal
                ↓
        READY_FOR_PROPOSAL

    The service does NOT:
        - discover the opportunity;
        - rank instruments;
        - perform position sizing;
        - perform portfolio simulation;
        - make a CIO decision;
        - execute an order.
    """

    def __init__(
        self,
        store: Stage3Store,
    ) -> None:

        self.store = store

        self.builder = (
            TradeProposalBuilder()
        )

    def create_preliminary_proposal(
        self,
        opportunity_id: str,
        *,
        entry_type: str = "MARKET",
        entry_price: float | None = None,
        target_1: float | None = None,
        target_2: float | None = None,
    ) -> TradeProposal:
        """
        Build, persist and activate a preliminary TradeProposal.

        The latest successful PositionSizingResult for the opportunity
        is used as the canonical sizing input.
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

        if opportunity.status not in {
            OpportunityStatus.POSITION_SIZED,
            OpportunityStatus.READY_FOR_PROPOSAL,
        }:

            raise ValueError(
                "TradeOpportunity must be POSITION_SIZED "
                "before a TradeProposal can be created"
            )

        # -----------------------------------------------------
        # Latest sizing
        # -----------------------------------------------------

        sizing = (
            self.store.get_latest_position_sizing(
                opportunity_id
            )
        )

        if sizing is None:

            raise ValueError(
                "No PositionSizingResult exists for "
                f"{opportunity_id}"
            )

        if (
            sizing.opportunity_id
            != opportunity_id
        ):

            raise ValueError(
                "Latest PositionSizingResult does not belong "
                "to the supplied TradeOpportunity"
            )

        if not sizing.constraints_passed:

            raise ValueError(
                "Latest PositionSizingResult failed constraints"
            )

        # -----------------------------------------------------
        # Instrument still exists in Fineco cache
        # -----------------------------------------------------

        instrument = (
            self.store.get_fineco_instrument(
                sizing.instrument_id
            )
        )

        if instrument is None:

            raise ValueError(
                "Fineco instrument referenced by sizing "
                "is no longer available in the local cache: "
                f"{sizing.instrument_id}"
            )

        # -----------------------------------------------------
        # Build canonical proposal
        # -----------------------------------------------------

        proposal = (
            self.builder.build(
                opportunity=opportunity,
                sizing=sizing,
                entry_type=entry_type,
                entry_price=entry_price,
                target_1=target_1,
                target_2=target_2,
            )
        )

        # -----------------------------------------------------
        # Persist
        # -----------------------------------------------------

        self.store.save_trade_proposal(
            proposal
        )

        # -----------------------------------------------------
        # Advance lifecycle
        # -----------------------------------------------------

        updated = (
            self.store
            .mark_trade_opportunity_ready_for_proposal(
                opportunity_id,
                proposal.proposal_id,
            )
        )

        if updated is None:

            # Defensive guard. The opportunity was loaded above,
            # therefore this should never normally happen.
            raise RuntimeError(
                "TradeOpportunity disappeared while "
                "creating TradeProposal"
            )

        return proposal