from __future__ import annotations

from datetime import datetime, timezone

from app.cio.models import (
    Direction,
    InstrumentCandidate,
    OpportunityStatus,
    TradeOpportunity,
    TradingHorizon,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    18,
    10,
    0,
    tzinfo=timezone.utc,
)


def _opportunity() -> TradeOpportunity:

    return TradeOpportunity(
        opportunity_id="OPP-SMCI-RANK",
        snapshot_id="SNAP-TEST",
        created_at=NOW,
        updated_at=NOW,
        ticker="SMCI",
        direction=Direction.SHORT,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=5,
        expected_holding_max_days=15,
        confidence=0.80,
        thesis="Persisted selector test",
        status=(
            OpportunityStatus
            .READY_FOR_INSTRUMENT_SELECTION
        ),
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=3,
    )


def test_replace_and_list_instrument_candidates(
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

    candidates = [
        InstrumentCandidate(
            instrument_id="FIN-ORDINARY",
            opportunity_id=(
                opportunity.opportunity_id
            ),
            eligible=True,
            suitability_score=0.80,
        ),
        InstrumentCandidate(
            instrument_id="FIN-INVERSE",
            opportunity_id=(
                opportunity.opportunity_id
            ),
            eligible=True,
            suitability_score=0.79,
        ),
        InstrumentCandidate(
            instrument_id="FIN-INTRADAY",
            opportunity_id=(
                opportunity.opportunity_id
            ),
            eligible=False,
            rejection_reason=(
                "Intraday-only instrument"
            ),
            suitability_score=0.0,
        ),
    ]

    store.replace_instrument_candidates(
        opportunity.opportunity_id,
        candidates,
    )

    loaded = (
        store.list_instrument_candidates(
            opportunity.opportunity_id
        )
    )

    assert len(loaded) == 3

    assert (
        loaded[0].instrument_id
        == "FIN-ORDINARY"
    )

    assert (
        loaded[1].instrument_id
        == "FIN-INVERSE"
    )

    assert loaded[2].eligible is False


def test_replace_removes_previous_ranking(
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

    store.replace_instrument_candidates(
        opportunity.opportunity_id,
        [
            InstrumentCandidate(
                instrument_id="OLD",
                opportunity_id=(
                    opportunity.opportunity_id
                ),
                eligible=True,
                suitability_score=0.50,
            )
        ],
    )

    store.replace_instrument_candidates(
        opportunity.opportunity_id,
        [
            InstrumentCandidate(
                instrument_id="NEW",
                opportunity_id=(
                    opportunity.opportunity_id
                ),
                eligible=True,
                suitability_score=0.90,
            )
        ],
    )

    loaded = (
        store.list_instrument_candidates(
            opportunity.opportunity_id
        )
    )

    assert len(loaded) == 1

    assert (
        loaded[0].instrument_id
        == "NEW"
    )


def test_mark_opportunity_instruments_ranked(
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

    store.replace_instrument_candidates(
        opportunity.opportunity_id,
        [
            InstrumentCandidate(
                instrument_id="FIN-1",
                opportunity_id=(
                    opportunity.opportunity_id
                ),
                eligible=True,
                suitability_score=0.80,
            )
        ],
    )

    updated = (
        store.mark_trade_opportunity_instruments_ranked(
            opportunity.opportunity_id
        )
    )

    assert updated is not None

    assert (
        updated.status
        == OpportunityStatus
        .INSTRUMENTS_RANKED
    )


def test_cannot_rank_without_eligible_candidate(
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

    store.replace_instrument_candidates(
        opportunity.opportunity_id,
        [
            InstrumentCandidate(
                instrument_id="FIN-NO",
                opportunity_id=(
                    opportunity.opportunity_id
                ),
                eligible=False,
                rejection_reason=(
                    "Not compatible"
                ),
                suitability_score=0.0,
            )
        ],
    )

    try:

        store.mark_trade_opportunity_instruments_ranked(
            opportunity.opportunity_id
        )

        assert False

    except ValueError:

        pass


def test_ranked_opportunity_does_not_regress_on_broker_refresh(
    tmp_path,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    opportunity = (
        _opportunity()
    )

    data = (
        opportunity.model_dump()
    )

    data["status"] = (
        OpportunityStatus.INSTRUMENTS_RANKED
    )

    ranked = (
        TradeOpportunity.model_validate(
            data
        )
    )

    store.save_trade_opportunity(
        ranked
    )

    refreshed = (
        store.refresh_trade_opportunity_broker_state(
            ranked.opportunity_id
        )
    )

    assert refreshed is not None

    assert (
        refreshed.status
        == OpportunityStatus
        .INSTRUMENTS_RANKED
    )