from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.cio.models import (
    OpportunityStatus,
    PositionSizingResult,
    ProposalStatus,
    TradeOpportunity,
    TradeProposal,
)


class TradeProposalBuilder:
    """
    Deterministic preliminary Trade Proposal builder.

    The builder assembles an already validated TradeOpportunity and
    PositionSizingResult into a canonical TradeProposal.

    It does NOT:
      - discover opportunities;
      - select broker instruments;
      - calculate position size;
      - invent entry, stop or target levels;
      - perform portfolio simulation;
      - make the final CIO decision;
      - execute a broker order.

    Pipeline:

        TradeOpportunity
            POSITION_SIZED
                +
        PositionSizingResult
            constraints_passed=True
                ↓
        TradeProposal
            PROPOSED
    """

    def build(
        self,
        *,
        opportunity: TradeOpportunity,
        sizing: PositionSizingResult,
        entry_type: str = "MARKET",
        entry_price: float | None = None,
        target_1: float | None = None,
        target_2: float | None = None,
    ) -> TradeProposal:

        self._validate_inputs(
            opportunity=opportunity,
            sizing=sizing,
        )

        entry_type = (
            entry_type
            .strip()
            .upper()
        )

        if not entry_type:

            raise ValueError(
                "entry_type is required"
            )

        if (
            entry_price is not None
            and entry_price <= 0
        ):
            raise ValueError(
                "entry_price must be > 0"
            )

        if (
            target_1 is not None
            and target_1 <= 0
        ):
            raise ValueError(
                "target_1 must be > 0"
            )

        if (
            target_2 is not None
            and target_2 <= 0
        ):
            raise ValueError(
                "target_2 must be > 0"
            )

        now = datetime.now(
            timezone.utc
        )

        return TradeProposal(
            proposal_id=(
                self._new_proposal_id(
                    opportunity.ticker,
                    now,
                )
            ),

            opportunity_id=(
                opportunity.opportunity_id
            ),

            snapshot_id=(
                opportunity.snapshot_id
            ),

            created_at=now,

            ticker=(
                opportunity.ticker.upper()
            ),

            direction=(
                opportunity.direction
            ),

            instrument_id=(
                sizing.instrument_id
            ),

            sizing_id=(
                sizing.sizing_id
            ),

            execution_side=(
                sizing.execution_side
            ),

            quantity=(
                sizing.quantity
            ),

            reference_price=(
                sizing.reference_price
            ),

            currency=(
                sizing.currency
            ),

            # Preserve the FX conversion that was actually used by
            # PositionSizer. This allows later stages to validate
            # cross-currency funding/deployment deterministically.
            fx_to_eur=(
                sizing.fx_to_eur
            ),

            entry_type=(
                entry_type
            ),

            # Do not invent an execution price.
            #
            # MARKET means the actual broker execution price is not
            # known yet. reference_price remains the sizing reference.
            entry_price=(
                entry_price
            ),

            # Stop comes directly from PositionSizingResult when one
            # was supplied during sizing.
            stop_price=(
                sizing.stop_price
            ),

            # Targets are intentionally external inputs for now.
            # The builder must not synthesize market levels.
            target_1=(
                target_1
            ),

            target_2=(
                target_2
            ),

            gross_exposure_eur=(
                sizing.gross_exposure_eur
            ),

            # Preserve the capital/collateral requirement determined by
            # PositionSizer. This is the canonical funding requirement
            # for downstream CIO deployment checks.
            estimated_capital_required_eur=(
                sizing.estimated_capital_required_eur
            ),

            estimated_margin_eur=(
                sizing.estimated_margin_eur
            ),

            estimated_max_loss_eur=(
                sizing.estimated_max_loss_eur
            ),

            expected_holding_min_days=(
                opportunity
                .expected_holding_min_days
            ),

            expected_holding_max_days=(
                opportunity
                .expected_holding_max_days
            ),

            status=(
                ProposalStatus.PROPOSED
            ),
        )

    # =========================================================
    # Validation
    # =========================================================

    def _validate_inputs(
        self,
        *,
        opportunity: TradeOpportunity,
        sizing: PositionSizingResult,
    ) -> None:

        if opportunity.status not in {
            OpportunityStatus.POSITION_SIZED,
            OpportunityStatus.READY_FOR_PROPOSAL,
        }:

            raise ValueError(
                "TradeProposal requires an opportunity "
                "with status POSITION_SIZED"
            )

        if (
            sizing.opportunity_id
            != opportunity.opportunity_id
        ):

            raise ValueError(
                "PositionSizingResult does not belong "
                "to the supplied TradeOpportunity"
            )

        if not sizing.constraints_passed:

            raise ValueError(
                "Cannot build TradeProposal from a sizing "
                "that failed constraints"
            )

        if not sizing.instrument_id:

            raise ValueError(
                "PositionSizingResult instrument_id "
                "is required"
            )

        if sizing.quantity <= 0:

            raise ValueError(
                "PositionSizingResult quantity must be > 0"
            )

        if sizing.reference_price <= 0:

            raise ValueError(
                "PositionSizingResult reference_price "
                "must be > 0"
            )

    # =========================================================
    # IDs
    # =========================================================

    def _new_proposal_id(
        self,
        ticker: str,
        now: datetime,
    ) -> str:

        return (
            f"PROP-"
            f"{ticker.upper()}-"
            f"{now:%Y%m%d-%H%M%S}-"
            f"{uuid4().hex[:6]}"
        )