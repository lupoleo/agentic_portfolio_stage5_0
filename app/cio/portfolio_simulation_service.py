from __future__ import annotations

from app.cio.models import (
    OpportunityStatus,
    PortfolioSimulation,
)
from app.cio.portfolio_simulator import (
    PortfolioRiskSimulator,
)
from app.cio.storage import Stage3Store


class PortfolioSimulationService:
    """
    Stage 3 orchestration service for PortfolioSimulation.

    Pipeline:

        TradeOpportunity
            READY_FOR_PROPOSAL
                ↓
        latest persisted TradeProposal
                +
        persisted PortfolioSnapshot
                +
        persisted PortfolioRiskStateRecord
                +
        latest AccountState
                ↓
        PortfolioRiskSimulator
                ↓
        persist PortfolioSimulation

    The service does NOT execute a broker order and does not modify the
    real PortfolioSnapshot.

    Important account-state semantics
    ---------------------------------
    PortfolioSnapshot.account_state_id identifies the AccountState that
    was associated with the portfolio when the analytical snapshot was
    created. That reference is historical/provenance information.

    Portfolio simulation, however, is a decision-time operation. Cash,
    reserves and operator risk constraints must therefore come from the
    latest persisted AccountState whenever one exists.

    Only if no latest AccountState is available do we fall back to the
    AccountState referenced by the PortfolioSnapshot.
    """

    def __init__(
        self,
        store: Stage3Store,
        *,
        simulator: PortfolioRiskSimulator | None = None,
    ) -> None:

        self.store = store

        # Backward-compatible composition boundary.
        #
        # Direct construction remains the historical V1 path so frozen
        # tests and explicit callers do not acquire market-data I/O
        # implicitly. Operational CLI composition is upgraded in PF-1G.5
        # through build_canonical_portfolio_simulation_service().
        self.simulator = (
            simulator
            if simulator is not None
            else PortfolioRiskSimulator()
        )

    def create_simulation(
        self,
        opportunity_id: str,
    ) -> PortfolioSimulation:
        """
        Create and persist a PortfolioSimulation for the latest
        TradeProposal belonging to one opportunity.

        The PortfolioRiskState BEFORE is loaded automatically from
        the latest persisted PortfolioRiskStateRecord associated with
        the same PortfolioSnapshot used by the TradeProposal.

        Decision-time AccountState semantics:
        - use the latest persisted AccountState for current cash,
          reserves and operator risk constraints;
        - fall back to snapshot.account_state_id only when no latest
          AccountState exists.
        """

        # =====================================================
        # TradeOpportunity
        # =====================================================

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
                "before portfolio simulation"
            )

        # =====================================================
        # TradeProposal
        # =====================================================

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

        # =====================================================
        # PortfolioSnapshot
        # =====================================================

        snapshot = (
            self.store.get_portfolio_snapshot(
                proposal.snapshot_id
            )
        )

        if snapshot is None:

            raise ValueError(
                "PortfolioSnapshot not found: "
                f"{proposal.snapshot_id}. "
                "This opportunity may have been created with a "
                "legacy/manual snapshot reference."
            )

        # =====================================================
        # PortfolioRiskState BEFORE
        # =====================================================

        risk_state_record = (
            self.store.get_latest_portfolio_risk_state(
                snapshot.snapshot_id
            )
        )

        if risk_state_record is None:

            raise ValueError(
                "No PortfolioRiskStateRecord exists for "
                f"PortfolioSnapshot {snapshot.snapshot_id}. "
                "Run the Portfolio Analysis / Quant Engine first."
            )

        before = (
            risk_state_record.state
        )

        # =====================================================
        # Defensive consistency checks
        # =====================================================

        tolerance = 0.01

        if (
            abs(
                before.gross_exposure_eur
                - snapshot.gross_exposure_eur
            )
            > tolerance
        ):

            raise ValueError(
                "PortfolioRiskState gross exposure does not match "
                "PortfolioSnapshot gross exposure"
            )

        if (
            abs(
                before.net_exposure_eur
                - snapshot.net_exposure_eur
            )
            > tolerance
        ):

            raise ValueError(
                "PortfolioRiskState net exposure does not match "
                "PortfolioSnapshot net exposure"
            )

        # =====================================================
        # AccountState
        # =====================================================
        #
        # IMPORTANT:
        #
        # snapshot.account_state_id is historical provenance. It tells
        # us which AccountState was associated with the analytical
        # PortfolioSnapshot.
        #
        # The simulation is a decision-time operation, so current cash,
        # reserves and operator constraints must come from the latest
        # AccountState instead.
        #
        # This matters when the operator changes a constraint after the
        # PortfolioSnapshot was produced (for example max gross exposure
        # from 80% to 95%) without re-running the full Quant Engine.
        # =====================================================

        account_state = (
            self.store.get_latest_account_state()
        )

        # Defensive fallback for legacy databases where no "latest"
        # AccountState exists but the PortfolioSnapshot still references
        # a persisted state.
        if (
            account_state is None
            and snapshot.account_state_id
            is not None
        ):

            account_state = (
                self.store.get_account_state(
                    snapshot.account_state_id
                )
            )

        if account_state is None:

            raise ValueError(
                "No AccountState is available for "
                "portfolio simulation"
            )

        # =====================================================
        # Portfolio simulation
        # =====================================================

        simulation = (
            self.simulator.simulate(
                snapshot=snapshot,
                proposal=proposal,
                before=before,
                account_state=account_state,
            )
        )

        # =====================================================
        # Persistence
        # =====================================================

        self.store.save_portfolio_simulation(
            simulation
        )

        return simulation