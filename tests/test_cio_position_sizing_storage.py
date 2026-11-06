from __future__ import annotations

from datetime import datetime, timezone

from app.cio.models import (
    Currency,
    Direction,
    ExecutionSide,
    InstrumentCandidate,
    OpportunityStatus,
    PositionSizingResult,
    TradeOpportunity,
    TradingHorizon,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    18,
    15,
    0,
    tzinfo=timezone.utc,
)


def _opportunity():

    return TradeOpportunity(
        opportunity_id="OPP-SIZE",
        snapshot_id="SNAP-TEST",
        created_at=NOW,
        updated_at=NOW,
        ticker="SMCI",
        direction=Direction.SHORT,
        horizon=TradingHorizon.SWING,
        confidence=0.80,
        thesis="Sizing persistence test",
        status=(
            OpportunityStatus
            .INSTRUMENTS_RANKED
        ),
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=1,
    )


def _sizing(
    *,
    passed=True,
):

    return PositionSizingResult(
        sizing_id="SIZ-001",
        opportunity_id="OPP-SIZE",
        instrument_id="FIN-001",
        created_at=NOW,
        execution_side=(
            ExecutionSide.SELL_SHORT
        ),
        reference_price=40,
        currency=Currency.USD,
        fx_to_eur=0.86,
        stop_price=44,
        quantity=100,
        gross_exposure_eur=3440,
        estimated_capital_required_eur=3440,
        estimated_margin_eur=None,
        estimated_max_loss_eur=344,
        risk_budget_eur=500,
        constraints_passed=passed,
        violated_constraints=(
            []
            if passed
            else ["Test violation"]
        ),
    )


def test_save_and_get_position_sizing(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    sizing = _sizing()

    store.save_position_sizing(
        sizing
    )

    loaded = (
        store.get_position_sizing(
            sizing.sizing_id
        )
    )

    assert loaded is not None

    assert (
        loaded.execution_side
        == ExecutionSide.SELL_SHORT
    )

    assert loaded.quantity == 100


def test_latest_position_sizing(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    sizing = _sizing()

    store.save_position_sizing(
        sizing
    )

    latest = (
        store.get_latest_position_sizing(
            "OPP-SIZE"
        )
    )

    assert latest is not None

    assert latest.sizing_id == "SIZ-001"


def test_top_candidate_returns_best_eligible(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    store.replace_instrument_candidates(
        "OPP-SIZE",
        [
            InstrumentCandidate(
                instrument_id="FIN-B",
                opportunity_id="OPP-SIZE",
                eligible=True,
                suitability_score=0.70,
            ),
            InstrumentCandidate(
                instrument_id="FIN-A",
                opportunity_id="OPP-SIZE",
                eligible=True,
                suitability_score=0.90,
            ),
        ],
    )

    top = (
        store.get_top_instrument_candidate(
            "OPP-SIZE"
        )
    )

    assert top is not None

    assert top.instrument_id == "FIN-A"


def test_passed_sizing_moves_opportunity_to_position_sized(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    opportunity = _opportunity()

    store.save_trade_opportunity(
        opportunity
    )

    sizing = _sizing()

    store.save_position_sizing(
        sizing
    )

    updated = (
        store.mark_trade_opportunity_position_sized(
            opportunity.opportunity_id,
            sizing.sizing_id,
        )
    )

    assert updated is not None

    assert (
        updated.status
        == OpportunityStatus.POSITION_SIZED
    )


def test_failed_sizing_cannot_advance_lifecycle(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    opportunity = _opportunity()

    store.save_trade_opportunity(
        opportunity
    )

    sizing = _sizing(
        passed=False
    )

    store.save_position_sizing(
        sizing
    )

    try:

        store.mark_trade_opportunity_position_sized(
            opportunity.opportunity_id,
            sizing.sizing_id,
        )

        assert False

    except ValueError:

        pass