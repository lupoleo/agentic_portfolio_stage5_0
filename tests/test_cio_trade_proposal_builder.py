from __future__ import annotations

from datetime import datetime, timezone

from app.cio.models import (
    Currency,
    Direction,
    ExecutionSide,
    OpportunityStatus,
    PositionSizingResult,
    ProposalStatus,
    TradeOpportunity,
    TradingHorizon,
)
from app.cio.trade_proposal_builder import (
    TradeProposalBuilder,
)


NOW = datetime(
    2026,
    8,
    18,
    19,
    0,
    tzinfo=timezone.utc,
)


def _opportunity(
    *,
    opportunity_id: str = "OPP-TEST",
    ticker: str = "SMCI",
    direction: Direction = Direction.SHORT,
    status: OpportunityStatus = (
        OpportunityStatus.POSITION_SIZED
    ),
) -> TradeOpportunity:

    return TradeOpportunity(
        opportunity_id=opportunity_id,
        snapshot_id="SNAP-TEST",
        created_at=NOW,
        updated_at=NOW,
        ticker=ticker,
        direction=direction,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=5,
        expected_holding_max_days=15,
        confidence=0.80,
        target_exposure_eur=5000,
        max_intended_loss_eur=6000,
        thesis="Trade proposal builder test",
        status=status,
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=10,
    )


def _sizing(
    *,
    opportunity_id: str = "OPP-TEST",
    instrument_id: str = "FIN-SMCI-ORDINARY",
    execution_side: ExecutionSide = (
        ExecutionSide.SELL_SHORT
    ),
    constraints_passed: bool = True,
    stop_price: float | None = None,
) -> PositionSizingResult:

    return PositionSizingResult(
        sizing_id="SIZ-TEST",
        opportunity_id=opportunity_id,
        instrument_id=instrument_id,
        created_at=NOW,
        execution_side=execution_side,
        reference_price=36.72,
        currency=Currency.USD,
        fx_to_eur=0.86,
        stop_price=stop_price,
        quantity=158,
        gross_exposure_eur=4989.51,
        estimated_capital_required_eur=4989.51,
        estimated_margin_eur=None,
        estimated_max_loss_eur=(
            250.00
            if stop_price is not None
            else None
        ),
        risk_budget_eur=6000,
        constraints_passed=(
            constraints_passed
        ),
        violated_constraints=(
            []
            if constraints_passed
            else [
                "Test sizing constraint violation"
            ]
        ),
    )


# =============================================================
# Basic proposal construction
# =============================================================


def test_build_short_direct_proposal():

    builder = TradeProposalBuilder()

    opportunity = _opportunity(
        direction=Direction.SHORT,
    )

    sizing = _sizing(
        execution_side=(
            ExecutionSide.SELL_SHORT
        )
    )

    proposal = builder.build(
        opportunity=opportunity,
        sizing=sizing,
    )

    assert (
        proposal.opportunity_id
        == opportunity.opportunity_id
    )

    assert (
        proposal.snapshot_id
        == opportunity.snapshot_id
    )

    assert proposal.ticker == "SMCI"

    assert (
        proposal.direction
        == Direction.SHORT
    )

    assert (
        proposal.instrument_id
        == sizing.instrument_id
    )

    assert (
        proposal.sizing_id
        == sizing.sizing_id
    )

    assert (
        proposal.execution_side
        == ExecutionSide.SELL_SHORT
    )

    assert proposal.quantity == 158

    assert (
        proposal.reference_price
        == 36.72
    )

    assert (
        proposal.currency
        == Currency.USD
    )

    assert (
        proposal.entry_type
        == "MARKET"
    )

    assert proposal.entry_price is None

    assert (
        proposal.status
        == ProposalStatus.PROPOSED
    )


# =============================================================
# LONG
# =============================================================


def test_build_long_buy_proposal():

    builder = TradeProposalBuilder()

    opportunity = _opportunity(
        ticker="NVDA",
        direction=Direction.LONG,
    )

    sizing = _sizing(
        instrument_id="FIN-NVDA-ORDINARY",
        execution_side=ExecutionSide.BUY,
    )

    proposal = builder.build(
        opportunity=opportunity,
        sizing=sizing,
    )

    assert (
        proposal.direction
        == Direction.LONG
    )

    assert (
        proposal.execution_side
        == ExecutionSide.BUY
    )

    assert (
        proposal.instrument_id
        == "FIN-NVDA-ORDINARY"
    )


# =============================================================
# SHORT through inverse instrument
# =============================================================


def test_build_short_inverse_product_uses_buy():

    builder = TradeProposalBuilder()

    opportunity = _opportunity(
        ticker="SMCI",
        direction=Direction.SHORT,
    )

    sizing = _sizing(
        instrument_id="FIN-SMCI-INVERSE-ETF",
        execution_side=ExecutionSide.BUY,
    )

    proposal = builder.build(
        opportunity=opportunity,
        sizing=sizing,
    )

    assert (
        proposal.direction
        == Direction.SHORT
    )

    assert (
        proposal.execution_side
        == ExecutionSide.BUY
    )

    assert (
        proposal.instrument_id
        == "FIN-SMCI-INVERSE-ETF"
    )


# =============================================================
# Propagation of sizing / opportunity data
# =============================================================


def test_builder_propagates_stop_and_holding_period():

    builder = TradeProposalBuilder()

    opportunity = _opportunity()

    sizing = _sizing(
        stop_price=40.00,
    )

    proposal = builder.build(
        opportunity=opportunity,
        sizing=sizing,
    )

    assert (
        proposal.stop_price
        == 40.00
    )

    assert (
        proposal.expected_holding_min_days
        == 5
    )

    assert (
        proposal.expected_holding_max_days
        == 15
    )

    assert (
        proposal.gross_exposure_eur
        == sizing.gross_exposure_eur
    )

    assert (
        proposal.estimated_max_loss_eur
        == sizing.estimated_max_loss_eur
    )


def test_builder_accepts_explicit_entry_and_targets():

    builder = TradeProposalBuilder()

    opportunity = _opportunity()

    sizing = _sizing()

    proposal = builder.build(
        opportunity=opportunity,
        sizing=sizing,
        entry_type="LIMIT",
        entry_price=36.50,
        target_1=33.00,
        target_2=30.00,
    )

    assert (
        proposal.entry_type
        == "LIMIT"
    )

    assert (
        proposal.entry_price
        == 36.50
    )

    assert proposal.target_1 == 33.00
    assert proposal.target_2 == 30.00


# =============================================================
# Validation
# =============================================================


def test_builder_rejects_wrong_opportunity_status():

    builder = TradeProposalBuilder()

    opportunity = _opportunity(
        status=(
            OpportunityStatus
            .INSTRUMENTS_RANKED
        )
    )

    sizing = _sizing()

    try:

        builder.build(
            opportunity=opportunity,
            sizing=sizing,
        )

        assert False

    except ValueError as exc:

        assert (
            "POSITION_SIZED"
            in str(exc)
        )


def test_builder_rejects_sizing_for_other_opportunity():

    builder = TradeProposalBuilder()

    opportunity = _opportunity(
        opportunity_id="OPP-A",
    )

    sizing = _sizing(
        opportunity_id="OPP-B",
    )

    try:

        builder.build(
            opportunity=opportunity,
            sizing=sizing,
        )

        assert False

    except ValueError as exc:

        assert (
            "does not belong"
            in str(exc)
        )


def test_builder_rejects_failed_sizing():

    builder = TradeProposalBuilder()

    opportunity = _opportunity()

    sizing = _sizing(
        constraints_passed=False,
    )

    try:

        builder.build(
            opportunity=opportunity,
            sizing=sizing,
        )

        assert False

    except ValueError as exc:

        assert (
            "failed constraints"
            in str(exc)
        )


def test_builder_rejects_empty_entry_type():

    builder = TradeProposalBuilder()

    opportunity = _opportunity()

    sizing = _sizing()

    try:

        builder.build(
            opportunity=opportunity,
            sizing=sizing,
            entry_type="   ",
        )

        assert False

    except ValueError as exc:

        assert (
            "entry_type is required"
            in str(exc)
        )


def test_builder_rejects_invalid_entry_price():

    builder = TradeProposalBuilder()

    opportunity = _opportunity()

    sizing = _sizing()

    try:

        builder.build(
            opportunity=opportunity,
            sizing=sizing,
            entry_type="LIMIT",
            entry_price=0,
        )

        assert False

    except ValueError as exc:

        assert (
            "entry_price must be > 0"
            in str(exc)
        )