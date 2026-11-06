from __future__ import annotations

from datetime import datetime, timezone

from app.cio.models import (
    CacheStatus,
    Currency,
    DataSource,
    Direction,
    ExecutionSide,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentType,
    OpportunityStatus,
    PositionSizingResult,
    ProposalStatus,
    TradeOpportunity,
    TradingHorizon,
    TradingMode,
)
from app.cio.storage import Stage3Store
from app.cio.trade_proposal_service import (
    TradeProposalService,
)


NOW = datetime(
    2026,
    8,
    18,
    20,
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
        opportunity_id="OPP-SMCI-SERVICE",
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
        thesis="TradeProposalService test",
        status=status,
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=1,
    )


def _instrument() -> FinecoInstrument:

    return FinecoInstrument(
        instrument_id="FIN-SMCI-ORDINARY",
        underlying="SMCI",
        reference_underlying="SMCI",
        exposure_relationship=(
            ExposureRelationship.DIRECT
        ),
        description=(
            "Super Micro Computer Ordinary NASDAQ"
        ),
        instrument_type=(
            InstrumentType.ORDINARY
        ),
        trading_mode=(
            TradingMode.ORDINARY
        ),
        fineco_symbol="SMCI.O",
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
        last_confirmed=NOW,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
    )


def _sizing(
    *,
    passed: bool = True,
) -> PositionSizingResult:

    return PositionSizingResult(
        sizing_id="SIZ-SMCI-SERVICE",
        opportunity_id="OPP-SMCI-SERVICE",
        instrument_id="FIN-SMCI-ORDINARY",
        created_at=NOW,
        execution_side=(
            ExecutionSide.SELL_SHORT
        ),
        reference_price=36.72,
        currency=Currency.USD,
        fx_to_eur=0.86,
        stop_price=None,
        quantity=158,
        gross_exposure_eur=4989.51,
        estimated_capital_required_eur=4989.51,
        estimated_margin_eur=None,
        estimated_max_loss_eur=None,
        risk_budget_eur=6000,
        constraints_passed=passed,
        violated_constraints=(
            []
            if passed
            else [
                "Test sizing failure"
            ]
        ),
    )


def _prepare_store(
    tmp_path,
    *,
    opportunity_status: OpportunityStatus = (
        OpportunityStatus.POSITION_SIZED
    ),
    sizing_passed: bool = True,
) -> Stage3Store:

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_trade_opportunity(
        _opportunity(
            status=opportunity_status
        )
    )

    store.save_fineco_instrument(
        _instrument()
    )

    store.save_position_sizing(
        _sizing(
            passed=sizing_passed
        )
    )

    return store


# =============================================================
# Happy path
# =============================================================


def test_service_creates_and_persists_proposal(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    service = TradeProposalService(
        store
    )

    proposal = (
        service.create_preliminary_proposal(
            "OPP-SMCI-SERVICE"
        )
    )

    assert (
        proposal.status
        == ProposalStatus.PROPOSED
    )

    assert (
        proposal.direction
        == Direction.SHORT
    )

    assert (
        proposal.execution_side
        == ExecutionSide.SELL_SHORT
    )

    assert proposal.quantity == 158

    persisted = (
        store.get_trade_proposal(
            proposal.proposal_id
        )
    )

    assert persisted is not None

    assert (
        persisted.proposal_id
        == proposal.proposal_id
    )


def test_service_moves_opportunity_to_ready_for_proposal(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    service = TradeProposalService(
        store
    )

    service.create_preliminary_proposal(
        "OPP-SMCI-SERVICE"
    )

    opportunity = (
        store.get_trade_opportunity(
            "OPP-SMCI-SERVICE"
        )
    )

    assert opportunity is not None

    assert (
        opportunity.status
        == OpportunityStatus.READY_FOR_PROPOSAL
    )


def test_service_uses_latest_sizing(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    later = datetime(
        2026,
        8,
        18,
        20,
        30,
        tzinfo=timezone.utc,
    )

    data = (
        _sizing()
        .model_dump()
    )

    data.update(
        {
            "sizing_id":
                "SIZ-SMCI-LATEST",

            "created_at":
                later,

            "quantity":
                100,

            "gross_exposure_eur":
                3157.92,

            "estimated_capital_required_eur":
                3157.92,
        }
    )

    latest = (
        PositionSizingResult.model_validate(
            data
        )
    )

    store.save_position_sizing(
        latest
    )

    service = TradeProposalService(
        store
    )

    proposal = (
        service.create_preliminary_proposal(
            "OPP-SMCI-SERVICE"
        )
    )

    assert proposal.quantity == 100

    assert (
        proposal.sizing_id
        == "SIZ-SMCI-LATEST"
    )


# =============================================================
# Optional proposal levels
# =============================================================


def test_service_passes_entry_and_targets_to_builder(
    tmp_path,
):

    store = _prepare_store(
        tmp_path
    )

    service = TradeProposalService(
        store
    )

    proposal = (
        service.create_preliminary_proposal(
            "OPP-SMCI-SERVICE",
            entry_type="LIMIT",
            entry_price=36.50,
            target_1=33.00,
            target_2=30.00,
        )
    )

    assert proposal.entry_type == "LIMIT"
    assert proposal.entry_price == 36.50
    assert proposal.target_1 == 33.00
    assert proposal.target_2 == 30.00


# =============================================================
# Validation
# =============================================================


def test_service_rejects_non_position_sized_opportunity(
    tmp_path,
):

    store = _prepare_store(
        tmp_path,
        opportunity_status=(
            OpportunityStatus.INSTRUMENTS_RANKED
        ),
    )

    service = TradeProposalService(
        store
    )

    try:

        service.create_preliminary_proposal(
            "OPP-SMCI-SERVICE"
        )

        assert False

    except ValueError as exc:

        assert (
            "POSITION_SIZED"
            in str(exc)
        )


def test_service_rejects_failed_latest_sizing(
    tmp_path,
):

    store = _prepare_store(
        tmp_path,
        sizing_passed=False,
    )

    service = TradeProposalService(
        store
    )

    try:

        service.create_preliminary_proposal(
            "OPP-SMCI-SERVICE"
        )

        assert False

    except ValueError as exc:

        assert (
            "failed constraints"
            in str(exc)
        )


def test_service_requires_fineco_instrument(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.save_trade_opportunity(
        _opportunity()
    )

    store.save_position_sizing(
        _sizing()
    )

    service = TradeProposalService(
        store
    )

    try:

        service.create_preliminary_proposal(
            "OPP-SMCI-SERVICE"
        )

        assert False

    except ValueError as exc:

        assert (
            "Fineco instrument"
            in str(exc)
        )