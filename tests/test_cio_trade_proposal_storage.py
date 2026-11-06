from __future__ import annotations

from datetime import datetime, timezone

from app.cio.models import (
    Currency,
    Direction,
    OpportunityStatus,
    ProposalStatus,
    TradeOpportunity,
    TradeProposal,
    TradingHorizon,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    18,
    18,
    0,
    tzinfo=timezone.utc,
)


def _opportunity(
    *,
    status: OpportunityStatus = (
        OpportunityStatus.POSITION_SIZED
    ),
) -> TradeOpportunity:

    return TradeOpportunity(
        opportunity_id="OPP-PROP-001",
        snapshot_id="SNAP-TEST",
        created_at=NOW,
        updated_at=NOW,
        ticker="SMCI",
        direction=Direction.SHORT,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=5,
        expected_holding_max_days=15,
        confidence=0.80,
        target_exposure_eur=5000,
        max_intended_loss_eur=6000,
        thesis="Trade proposal storage test",
        status=status,
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=10,
    )


def _proposal(
    *,
    proposal_id: str = "PROP-001",
    opportunity_id: str = "OPP-PROP-001",
    snapshot_id: str = "SNAP-TEST",
    status: ProposalStatus = (
        ProposalStatus.PROPOSED
    ),
) -> TradeProposal:

    return TradeProposal(
        proposal_id=proposal_id,
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        created_at=NOW,
        ticker="SMCI",
        direction=Direction.SHORT,
        instrument_id=(
            "FIN-SMCI-ORDINARY-ORDINARY-X1-aca02e"
        ),
        quantity=158,
        reference_price=36.72,
        currency=Currency.USD,
        entry_type="MARKET",
        entry_price=None,
        stop_price=None,
        target_1=None,
        target_2=None,
        gross_exposure_eur=4989.51,
        estimated_margin_eur=None,
        estimated_max_loss_eur=None,
        expected_holding_min_days=5,
        expected_holding_max_days=15,
        status=status,
    )


# =============================================================
# Persistence
# =============================================================


def test_save_and_get_trade_proposal(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    proposal = _proposal()

    store.save_trade_proposal(
        proposal
    )

    loaded = (
        store.get_trade_proposal(
            proposal.proposal_id
        )
    )

    assert loaded is not None

    assert (
        loaded.proposal_id
        == "PROP-001"
    )

    assert (
        loaded.opportunity_id
        == "OPP-PROP-001"
    )

    assert (
        loaded.instrument_id
        == (
            "FIN-SMCI-ORDINARY-"
            "ORDINARY-X1-aca02e"
        )
    )

    assert (
        loaded.direction
        == Direction.SHORT
    )

    assert loaded.quantity == 158

    assert (
        loaded.status
        == ProposalStatus.PROPOSED
    )


def test_list_trade_proposals_for_opportunity(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    first = _proposal(
        proposal_id="PROP-001"
    )

    second = _proposal(
        proposal_id="PROP-002"
    )

    store.save_trade_proposal(
        first
    )

    store.save_trade_proposal(
        second
    )

    proposals = (
        store.list_trade_proposals(
            "OPP-PROP-001"
        )
    )

    assert len(proposals) == 2

    ids = {
        proposal.proposal_id
        for proposal in proposals
    }

    assert ids == {
        "PROP-001",
        "PROP-002",
    }


def test_latest_trade_proposal(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    first = _proposal(
        proposal_id="PROP-OLD"
    )

    store.save_trade_proposal(
        first
    )

    later_time = datetime(
        2026,
        8,
        18,
        18,
        30,
        tzinfo=timezone.utc,
    )

    data = (
        _proposal(
            proposal_id="PROP-NEW"
        )
        .model_dump()
    )

    data["created_at"] = (
        later_time
    )

    second = (
        TradeProposal.model_validate(
            data
        )
    )

    store.save_trade_proposal(
        second
    )

    latest = (
        store.get_latest_trade_proposal(
            "OPP-PROP-001"
        )
    )

    assert latest is not None

    assert (
        latest.proposal_id
        == "PROP-NEW"
    )


# =============================================================
# Lifecycle
# =============================================================


def test_proposal_moves_opportunity_to_ready_for_proposal(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    opportunity = (
        _opportunity()
    )

    store.save_trade_opportunity(
        opportunity
    )

    proposal = (
        _proposal()
    )

    store.save_trade_proposal(
        proposal
    )

    updated = (
        store.mark_trade_opportunity_ready_for_proposal(
            opportunity.opportunity_id,
            proposal.proposal_id,
        )
    )

    assert updated is not None

    assert (
        updated.status
        == OpportunityStatus.READY_FOR_PROPOSAL
    )


def test_cannot_mark_ready_without_persisted_proposal(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    opportunity = (
        _opportunity()
    )

    store.save_trade_opportunity(
        opportunity
    )

    try:

        store.mark_trade_opportunity_ready_for_proposal(
            opportunity.opportunity_id,
            "PROP-MISSING",
        )

        assert False

    except ValueError:

        pass


def test_cannot_mark_ready_with_proposal_for_other_opportunity(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    opportunity = (
        _opportunity()
    )

    store.save_trade_opportunity(
        opportunity
    )

    proposal = (
        _proposal(
            proposal_id="PROP-WRONG",
            opportunity_id="OPP-OTHER",
        )
    )

    store.save_trade_proposal(
        proposal
    )

    try:

        store.mark_trade_opportunity_ready_for_proposal(
            opportunity.opportunity_id,
            proposal.proposal_id,
        )

        assert False

    except ValueError:

        pass


def test_ready_for_proposal_does_not_regress_on_broker_refresh(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    opportunity = (
        _opportunity(
            status=(
                OpportunityStatus
                .READY_FOR_PROPOSAL
            )
        )
    )

    store.save_trade_opportunity(
        opportunity
    )

    refreshed = (
        store.refresh_trade_opportunity_broker_state(
            opportunity.opportunity_id
        )
    )

    assert refreshed is not None

    assert (
        refreshed.status
        == OpportunityStatus.READY_FOR_PROPOSAL
    )