from __future__ import annotations

from datetime import datetime, timezone

from app.cio.models import (
    CacheStatus,
    Currency,
    DataSource,
    Direction,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentType,
    OpportunityStatus,
    TradeOpportunity,
    TradingHorizon,
    TradingMode,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    18,
    8,
    0,
    0,
    tzinfo=timezone.utc,
)


# =============================================================
# Helpers
# =============================================================


def _make_opportunity(
    *,
    opportunity_id: str = "OPP-001",
    ticker: str = "AVGO",
    direction: Direction = Direction.LONG,
    status: OpportunityStatus = OpportunityStatus.DISCOVERED,
    broker_instruments_available: bool | None = None,
    broker_instrument_count: int | None = None,
) -> TradeOpportunity:
    return TradeOpportunity(
        opportunity_id=opportunity_id,
        snapshot_id="SNAP-001",
        created_at=NOW,
        updated_at=None,
        ticker=ticker,
        direction=direction,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=5,
        expected_holding_max_days=15,
        confidence=0.80,
        target_exposure_eur=5000,
        max_intended_loss_eur=500,
        thesis="Test trading opportunity",
        catalyst="Test catalyst",
        key_risks=[
            "Market risk",
        ],
        evidence_ids=[],
        status=status,
        broker_instruments_required=True,
        broker_instruments_available=(
            broker_instruments_available
        ),
        broker_instrument_count=(
            broker_instrument_count
        ),
        rejection_reason=(
            "Rejected for test"
            if status == OpportunityStatus.REJECTED
            else None
        ),
        expiry_reason=(
            "Expired for test"
            if status == OpportunityStatus.EXPIRED
            else None
        ),
        notes="pytest opportunity",
    )


def _make_fineco_instrument(
    *,
    instrument_id: str = "FIN-AVGO-ORDINARY",
    underlying: str = "AVGO",
) -> FinecoInstrument:
    return FinecoInstrument(
        instrument_id=instrument_id,
        underlying=underlying,
        reference_underlying=underlying,
        exposure_relationship=(
            ExposureRelationship.DIRECT
        ),
        description=(
            f"{underlying} Ordinary"
        ),
        instrument_type=(
            InstrumentType.ORDINARY
        ),
        trading_mode=(
            TradingMode.ORDINARY
        ),
        fineco_symbol=underlying,
        market="NASDAQ",
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        margin_currency=None,
        long_available=True,
        short_available=True,
        intraday_available=True,
        overnight_available=True,
        broker_leverage=1,
        embedded_leverage=1,
        margin_pct=None,
        tick_size=0.01,
        tick_currency=Currency.USD,
        commission=None,
        overnight_financing_pct=None,
        certificate_terms=None,
        market_snapshot=None,
        last_confirmed=NOW,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
        notes="pytest instrument",
    )


# =============================================================
# Persistence
# =============================================================


def test_save_and_get_long_opportunity(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        direction=Direction.LONG,
    )

    store.save_trade_opportunity(
        opportunity
    )

    loaded = (
        store.get_trade_opportunity(
            opportunity.opportunity_id
        )
    )

    assert loaded is not None

    assert (
        loaded.opportunity_id
        == opportunity.opportunity_id
    )

    assert (
        loaded.ticker
        == "AVGO"
    )

    assert (
        loaded.direction
        == Direction.LONG
    )

    assert (
        loaded.status
        == OpportunityStatus.DISCOVERED
    )


def test_save_and_get_short_opportunity(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        opportunity_id="OPP-SHORT-001",
        ticker="SMCI",
        direction=Direction.SHORT,
    )

    store.save_trade_opportunity(
        opportunity
    )

    loaded = (
        store.get_trade_opportunity(
            opportunity.opportunity_id
        )
    )

    assert loaded is not None

    assert (
        loaded.direction
        == Direction.SHORT
    )

    assert (
        loaded.ticker
        == "SMCI"
    )


# =============================================================
# Listing / search
# =============================================================


def test_list_trade_opportunities(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    store.save_trade_opportunity(
        _make_opportunity(
            opportunity_id="OPP-001",
            ticker="AVGO",
            direction=Direction.LONG,
        )
    )

    store.save_trade_opportunity(
        _make_opportunity(
            opportunity_id="OPP-002",
            ticker="SMCI",
            direction=Direction.SHORT,
        )
    )

    opportunities = (
        store.list_trade_opportunities()
    )

    assert len(
        opportunities
    ) == 2


def test_find_trade_opportunities_by_ticker(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    store.save_trade_opportunity(
        _make_opportunity(
            opportunity_id="OPP-AVGO-LONG",
            ticker="AVGO",
            direction=Direction.LONG,
        )
    )

    store.save_trade_opportunity(
        _make_opportunity(
            opportunity_id="OPP-AVGO-SHORT",
            ticker="AVGO",
            direction=Direction.SHORT,
        )
    )

    store.save_trade_opportunity(
        _make_opportunity(
            opportunity_id="OPP-SMCI",
            ticker="SMCI",
            direction=Direction.LONG,
        )
    )

    opportunities = (
        store.find_trade_opportunities(
            "avgo"
        )
    )

    assert len(
        opportunities
    ) == 2

    directions = {
        item.direction
        for item in opportunities
    }

    assert directions == {
        Direction.LONG,
        Direction.SHORT,
    }


# =============================================================
# Missing broker instruments
# =============================================================


def test_refresh_missing_instruments_moves_to_waiting(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        opportunity_id="OPP-AVGO",
        ticker="AVGO",
        direction=Direction.LONG,
    )

    store.save_trade_opportunity(
        opportunity
    )

    updated = (
        store.refresh_trade_opportunity_broker_state(
            opportunity.opportunity_id
        )
    )

    assert updated is not None

    assert (
        updated.status
        == OpportunityStatus
        .WAITING_FOR_BROKER_INSTRUMENTS
    )

    assert (
        updated.broker_instruments_available
        is False
    )

    assert (
        updated.broker_instrument_count
        == 0
    )

    assert (
        updated.updated_at
        is not None
    )


# =============================================================
# Existing broker instruments
# =============================================================


def test_refresh_existing_instruments_moves_to_ready(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        opportunity_id="OPP-AVGO",
        ticker="AVGO",
    )

    store.save_trade_opportunity(
        opportunity
    )

    store.save_fineco_instrument(
        _make_fineco_instrument(
            underlying="AVGO",
        )
    )

    updated = (
        store.refresh_trade_opportunity_broker_state(
            opportunity.opportunity_id
        )
    )

    assert updated is not None

    assert (
        updated.status
        == OpportunityStatus
        .READY_FOR_INSTRUMENT_SELECTION
    )

    assert (
        updated.broker_instruments_available
        is True
    )

    assert (
        updated.broker_instrument_count
        == 1
    )


# =============================================================
# WAITING -> READY transition
# =============================================================


def test_waiting_opportunity_becomes_ready_after_instrument_import(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        opportunity_id="OPP-AVGO",
        ticker="AVGO",
    )

    store.save_trade_opportunity(
        opportunity
    )

    first = (
        store.refresh_trade_opportunity_broker_state(
            opportunity.opportunity_id
        )
    )

    assert first is not None

    assert (
        first.status
        == OpportunityStatus
        .WAITING_FOR_BROKER_INSTRUMENTS
    )

    store.save_fineco_instrument(
        _make_fineco_instrument(
            underlying="AVGO",
        )
    )

    refreshed = (
        store.refresh_waiting_trade_opportunities()
    )

    assert len(
        refreshed
    ) == 1

    updated = (
        refreshed[0]
    )

    assert (
        updated.opportunity_id
        == opportunity.opportunity_id
    )

    assert (
        updated.status
        == OpportunityStatus
        .READY_FOR_INSTRUMENT_SELECTION
    )

    assert (
        updated.broker_instruments_available
        is True
    )

    assert (
        updated.broker_instrument_count
        == 1
    )


# =============================================================
# REJECTED must not regress
# =============================================================


def test_rejected_opportunity_does_not_regress(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        opportunity_id="OPP-REJECTED",
        ticker="AVGO",
        status=OpportunityStatus.REJECTED,
    )

    store.save_trade_opportunity(
        opportunity
    )

    store.save_fineco_instrument(
        _make_fineco_instrument(
            underlying="AVGO",
        )
    )

    updated = (
        store.refresh_trade_opportunity_broker_state(
            opportunity.opportunity_id
        )
    )

    assert updated is not None

    assert (
        updated.status
        == OpportunityStatus.REJECTED
    )

    assert (
        updated.rejection_reason
        == "Rejected for test"
    )


# =============================================================
# EXPIRED must not regress
# =============================================================


def test_expired_opportunity_does_not_regress(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        opportunity_id="OPP-EXPIRED",
        ticker="AVGO",
        status=OpportunityStatus.EXPIRED,
    )

    store.save_trade_opportunity(
        opportunity
    )

    store.save_fineco_instrument(
        _make_fineco_instrument(
            underlying="AVGO",
        )
    )

    updated = (
        store.refresh_trade_opportunity_broker_state(
            opportunity.opportunity_id
        )
    )

    assert updated is not None

    assert (
        updated.status
        == OpportunityStatus.EXPIRED
    )

    assert (
        updated.expiry_reason
        == "Expired for test"
    )


# =============================================================
# READY_FOR_PROPOSAL must not regress
# =============================================================


def test_ready_for_proposal_does_not_regress(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        opportunity_id="OPP-PROPOSAL",
        ticker="AVGO",
        status=OpportunityStatus.READY_FOR_PROPOSAL,
        broker_instruments_available=True,
        broker_instrument_count=1,
    )

    store.save_trade_opportunity(
        opportunity
    )

    store.save_fineco_instrument(
        _make_fineco_instrument(
            underlying="AVGO",
        )
    )

    updated = (
        store.refresh_trade_opportunity_broker_state(
            opportunity.opportunity_id
        )
    )

    assert updated is not None

    assert (
        updated.status
        == OpportunityStatus.READY_FOR_PROPOSAL
    )


# =============================================================
# Status filter
# =============================================================


def test_list_trade_opportunities_by_status(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    waiting = _make_opportunity(
        opportunity_id="OPP-WAITING",
        ticker="AVGO",
        status=(
            OpportunityStatus
            .WAITING_FOR_BROKER_INSTRUMENTS
        ),
        broker_instruments_available=False,
        broker_instrument_count=0,
    )

    discovered = _make_opportunity(
        opportunity_id="OPP-DISCOVERED",
        ticker="SMCI",
        status=(
            OpportunityStatus.DISCOVERED
        ),
    )

    store.save_trade_opportunity(
        waiting
    )

    store.save_trade_opportunity(
        discovered
    )

    result = (
        store.list_trade_opportunities(
            status=(
                OpportunityStatus
                .WAITING_FOR_BROKER_INSTRUMENTS
            )
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[0].opportunity_id
        == "OPP-WAITING"
    )


# =============================================================
# Delete
# =============================================================


def test_delete_trade_opportunity(
    tmp_path,
):
    db = tmp_path / "cio.db"

    store = Stage3Store(
        db
    )

    opportunity = _make_opportunity(
        opportunity_id="OPP-DELETE",
    )

    store.save_trade_opportunity(
        opportunity
    )

    deleted = (
        store.delete_trade_opportunity(
            opportunity.opportunity_id
        )
    )

    assert deleted is True

    assert (
        store.get_trade_opportunity(
            opportunity.opportunity_id
        )
        is None
    )

    deleted_again = (
        store.delete_trade_opportunity(
            opportunity.opportunity_id
        )
    )

    assert deleted_again is False