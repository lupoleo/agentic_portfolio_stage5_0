from __future__ import annotations

from datetime import datetime, timezone

from app.cio.instrument_selector import (
    InstrumentSelector,
)
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


NOW = datetime(
    2026,
    8,
    18,
    9,
    0,
    tzinfo=timezone.utc,
)


def _opportunity(
    *,
    direction: Direction,
    horizon: TradingHorizon,
) -> TradeOpportunity:

    return TradeOpportunity(
        opportunity_id="OPP-TEST",
        snapshot_id="SNAP-TEST",
        created_at=NOW,
        updated_at=NOW,
        ticker="TEST",
        direction=direction,
        horizon=horizon,
        expected_holding_min_days=(
            5
            if horizon != TradingHorizon.INTRADAY
            else 0
        ),
        expected_holding_max_days=(
            15
            if horizon != TradingHorizon.INTRADAY
            else 0
        ),
        confidence=0.80,
        thesis="Selector test",
        status=(
            OpportunityStatus
            .READY_FOR_INSTRUMENT_SELECTION
        ),
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=1,
    )


def _instrument(
    *,
    instrument_id: str,
    relationship: ExposureRelationship = (
        ExposureRelationship.DIRECT
    ),
    instrument_type: InstrumentType = (
        InstrumentType.ORDINARY
    ),
    mode: TradingMode = TradingMode.ORDINARY,
    long_available: bool | None = True,
    short_available: bool | None = True,
    intraday_available: bool | None = True,
    overnight_available: bool | None = True,
    broker_leverage: float | None = 1,
    embedded_leverage: float | None = 1,
) -> FinecoInstrument:

    return FinecoInstrument(
        instrument_id=instrument_id,
        underlying="TEST",
        reference_underlying="TEST",
        exposure_relationship=relationship,
        description=instrument_id,
        instrument_type=instrument_type,
        trading_mode=mode,
        fineco_symbol=instrument_id,
        market="TEST",
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        margin_currency=Currency.USD,
        long_available=long_available,
        short_available=short_available,
        intraday_available=intraday_available,
        overnight_available=overnight_available,
        broker_leverage=broker_leverage,
        embedded_leverage=embedded_leverage,
        margin_pct=None,
        tick_size=None,
        tick_currency=None,
        commission=None,
        overnight_financing_pct=None,
        certificate_terms=None,
        market_snapshot=None,
        last_confirmed=NOW,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
    )


# =============================================================
# LONG
# =============================================================


def test_long_direct_is_eligible():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.LONG,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="ORDINARY"
            )
        ],
    )

    assert len(result) == 1
    assert result[0].eligible is True


def test_long_inverse_is_rejected():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.LONG,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="INVERSE",
                relationship=(
                    ExposureRelationship.INVERSE
                ),
            )
        ],
    )

    assert result[0].eligible is False

    assert (
        "inverse"
        in result[0]
        .rejection_reason
        .lower()
    )


# =============================================================
# SHORT
# =============================================================


def test_short_direct_requires_short_availability():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.SHORT,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="NO-SHORT",
                short_available=False,
            )
        ],
    )

    assert result[0].eligible is False


def test_short_direct_is_eligible_when_short_available():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.SHORT,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="SHORT-OK",
                short_available=True,
            )
        ],
    )

    assert result[0].eligible is True


def test_short_inverse_long_product_is_eligible():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.SHORT,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="INVERSE-ETF",
                relationship=(
                    ExposureRelationship.INVERSE
                ),
                long_available=True,
            )
        ],
    )

    assert result[0].eligible is True


def test_short_leveraged_long_product_is_rejected():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.SHORT,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="LONG-2X",
                relationship=(
                    ExposureRelationship
                    .LEVERAGED_LONG
                ),
                embedded_leverage=2,
            )
        ],
    )

    assert result[0].eligible is False


# =============================================================
# Horizon
# =============================================================


def test_swing_rejects_intraday_only():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.LONG,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="INTRADAY",
                mode=TradingMode.INTRADAY,
                intraday_available=True,
                overnight_available=False,
            )
        ],
    )

    assert result[0].eligible is False

    assert (
        "multi-session"
        in result[0]
        .rejection_reason
        .lower()
    )


def test_swing_rejects_super_leverage():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.LONG,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="X50",
                mode=TradingMode.SUPER_LEVERAGE,
                broker_leverage=50,
            )
        ],
    )

    assert result[0].eligible is False


def test_swing_ordinary_is_eligible():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.LONG,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="ORDINARY",
                mode=TradingMode.ORDINARY,
            )
        ],
    )

    assert result[0].eligible is True


def test_intraday_mode_scores_above_ordinary_for_intraday():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.LONG,
            horizon=TradingHorizon.INTRADAY,
        ),
        [
            _instrument(
                instrument_id="ORDINARY",
                mode=TradingMode.ORDINARY,
            ),
            _instrument(
                instrument_id="INTRADAY",
                mode=TradingMode.INTRADAY,
                broker_leverage=1,
            ),
        ],
    )

    assert result[0].instrument_id == "INTRADAY"

    assert (
        result[0].suitability_score
        >
        result[1].suitability_score
    )


# =============================================================
# Risk / ranking
# =============================================================


def test_lower_leverage_scores_above_high_leverage_for_swing():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.LONG,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="X1",
                broker_leverage=1,
            ),
            _instrument(
                instrument_id="X5",
                mode=TradingMode.OVERNIGHT,
                broker_leverage=5,
            ),
        ],
    )

    assert result[0].instrument_id == "X1"

    assert (
        result[0].suitability_score
        >
        result[1].suitability_score
    )


def test_inverse_etf_can_rank_above_direct_short_for_short_swing():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.SHORT,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="DIRECT-SHORT",
                relationship=(
                    ExposureRelationship.DIRECT
                ),
            ),
            _instrument(
                instrument_id="INVERSE-ETF",
                relationship=(
                    ExposureRelationship.INVERSE
                ),
                instrument_type=(
                    InstrumentType.ETF
                ),
            ),
        ],
    )

    assert result[0].eligible is True
    assert result[0].instrument_id == "INVERSE-ETF"


def test_structured_short_is_rejected_without_payoff_classification():

    selector = InstrumentSelector()

    result = selector.select(
        _opportunity(
            direction=Direction.SHORT,
            horizon=TradingHorizon.SWING,
        ),
        [
            _instrument(
                instrument_id="CERTIFICATE",
                relationship=(
                    ExposureRelationship.STRUCTURED
                ),
                instrument_type=(
                    InstrumentType.CERTIFICATE
                ),
                embedded_leverage=None,
            )
        ],
    )

    assert result[0].eligible is False

    assert (
        "structured"
        in result[0]
        .rejection_reason
        .lower()
    )