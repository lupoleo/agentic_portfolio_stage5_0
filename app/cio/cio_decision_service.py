from __future__ import annotations

from app.cio.cio_decision_engine import (
    CioDecisionEngine,
)
from app.cio.models import (
    CioDecision,
    OpportunityStatus,
)
from app.cio.storage import Stage3Store


class CioDecisionService:
    """
    Stage 3 orchestration service for CIO decisions.

    Pipeline:

        TradeOpportunity
                |
                v
        latest persisted TradeProposal
                |
                v
        latest PortfolioSimulation
        for that exact proposal
                |
                v
        CioDecisionEngine
                |
                v
        persist CioDecision

    This service does not execute broker orders and does not modify
    the real PortfolioSnapshot.

    The Decision Engine remains deterministic in V1. A future AI
    reasoning layer may enrich the decision rationale and evidence,
    but deterministic hard constraints remain authoritative.
    """

    def __init__(
        self,
        store: Stage3Store,
    ) -> None:

        self.store = store
        self.engine = CioDecisionEngine()

    def create_decision(
        self,
        opportunity_id: str,
    ) -> CioDecision:
        """
        Create and persist a preliminary CIO decision for the latest
        TradeProposal belonging to one opportunity.

        The service deliberately resolves the PortfolioSimulation from
        the exact proposal selected here. It must never use an older
        simulation belonging to a previous proposal for the same
        opportunity.
        """

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

        if (
            opportunity.status
            != OpportunityStatus.READY_FOR_PROPOSAL
        ):
            raise ValueError(
                "TradeOpportunity must be READY_FOR_PROPOSAL "
                "before CIO decision"
            )

        proposal = (
            self.store.get_latest_trade_proposal(
                opportunity_id
            )
        )

        if proposal is None:
            raise ValueError(
                "No persisted TradeProposal exists for "
                f"{opportunity_id}"
            )

        if (
            proposal.opportunity_id
            != opportunity_id
        ):
            raise ValueError(
                "TradeProposal does not belong to the "
                "supplied TradeOpportunity"
            )

        if (
            proposal.snapshot_id
            != opportunity.snapshot_id
        ):
            raise ValueError(
                "TradeProposal snapshot_id does not match "
                "the TradeOpportunity snapshot_id"
            )

        simulations = (
            self.store.list_portfolio_simulations(
                proposal.proposal_id
            )
        )

        if not simulations:
            raise ValueError(
                "No persisted PortfolioSimulation exists for "
                f"TradeProposal {proposal.proposal_id}. "
                "Run portfolio simulation before CIO decision."
            )

        simulation = simulations[0]

        if (
            simulation.proposal_id
            != proposal.proposal_id
        ):
            raise ValueError(
                "PortfolioSimulation does not belong to the "
                "latest TradeProposal"
            )

        if (
            simulation.snapshot_id
            != proposal.snapshot_id
        ):
            raise ValueError(
                "PortfolioSimulation snapshot_id does not match "
                "the TradeProposal snapshot_id"
            )

        decision = self.engine.decide(
            proposal=proposal,
            simulation=simulation,
        )

        self.store.save_cio_decision(
            decision
        )

        return decision

    def get_latest_decision(
        self,
        opportunity_id: str,
    ) -> CioDecision | None:
        """
        Return the latest persisted CIO decision for one opportunity.
        """

        return (
            self.store.get_latest_cio_decision(
                opportunity_id
            )
        )