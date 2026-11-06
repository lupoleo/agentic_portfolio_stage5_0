from __future__ import annotations

from datetime import datetime, timezone

from app.cio.models import (
    AccountState,
    CacheStatus,
    Currency,
    CurrencyCash,
    DataSource,
    Direction,
    ExecutionSide,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentType,
    OpportunityStatus,
    RiskConstraints,
    TradeOpportunity,
    TradingHorizon,
    TradingMode,
)
from app.cio.position_sizer import PositionSizer


NOW = datetime(
    2026,
    8,
    18,
    12,
    0,
    tzinfo=timezone.utc,
)


def _account(
    *,
    eur: float = 20000,
    usd: float = 20000,
    eur_reserve: float = 0,
    usd_reserve: float = 0,
    max_loss: float | None = 1000,
    max_cio_deployable_pct: float | None = None,
) -> AccountState:

    return AccountState(
        account_state_id="ACC-TEST",
        timestamp=NOW,
        cash=[
            CurrencyCash(
                currency=Currency.EUR,
                available=eur,
                reserve=eur_reserve,
            ),
            CurrencyCash(
                currency=Currency.USD,
                available=usd,
                reserve=usd_reserve,
            ),
        ],
        constraints=RiskConstraints(
            max_trade_loss_eur=max_loss,
            max_cio_deployable_pct=(
                max_cio_deployable_pct
            ),
        ),
        source=DataSource.OPERATOR,
    )


def _opportunity(
    *,
    direction: Direction,
    exposure: float | None = 5000,
    max_loss: float | None = 500,
) -> TradeOpportunity:

    return TradeOpportunity(
        opportunity_id="OPP-TEST",
        snapshot_id="SNAP-TEST",
        created_at=NOW,
        updated_at=NOW,
        ticker="TEST",
        direction=direction,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=5,
        expected_holding_max_days=15,
        confidence=0.80,
        target_exposure_eur=exposure,
        max_intended_loss_eur=max_loss,
        thesis="Sizing test",
        status=OpportunityStatus.INSTRUMENTS_RANKED,
        broker_instruments_required=True,
        broker_instruments_available=True,
        broker_instrument_count=1,
    )


def _instrument(
    *,
    relationship=ExposureRelationship.DIRECT,
    instrument_type=InstrumentType.ORDINARY,
    broker_leverage=1,
    margin_pct=None,
    quote_currency=Currency.USD,
    settlement_currency=Currency.USD,
    margin_currency=None,
) -> FinecoInstrument:

    return FinecoInstrument(
        instrument_id="FIN-TEST",
        underlying="TEST",
        reference_underlying="TEST",
        exposure_relationship=relationship,
        description="Test instrument",
        instrument_type=instrument_type,
        trading_mode=(
            TradingMode.OVERNIGHT
            if instrument_type
            in {
                InstrumentType.MARGIN,
                InstrumentType.CFD,
                InstrumentType.CFDC,
            }
            else TradingMode.ORDINARY
        ),
        fineco_symbol="TEST",
        market="TEST",
        quote_currency=quote_currency,
        settlement_currency=settlement_currency,
        margin_currency=margin_currency,
        long_available=True,
        short_available=True,
        intraday_available=True,
        overnight_available=True,
        broker_leverage=broker_leverage,
        embedded_leverage=1,
        margin_pct=margin_pct,
        last_confirmed=NOW,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
    )


def test_long_direct_execution_is_buy():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.LONG,
        ),
        instrument=_instrument(),
        account_state=_account(),
        reference_price=100,
        fx_to_eur=1,
        stop_price=90,
    )

    assert (
        result.execution_side
        == ExecutionSide.BUY
    )


def test_short_direct_execution_is_sell_short():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.SHORT,
        ),
        instrument=_instrument(),
        account_state=_account(),
        reference_price=100,
        fx_to_eur=1,
        stop_price=110,
    )

    assert (
        result.execution_side
        == ExecutionSide.SELL_SHORT
    )


def test_short_inverse_etf_execution_is_buy():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.SHORT,
        ),
        instrument=_instrument(
            relationship=(
                ExposureRelationship.INVERSE
            ),
            instrument_type=(
                InstrumentType.ETF
            ),
        ),
        account_state=_account(),
        reference_price=100,
        fx_to_eur=1,
        stop_price=110,
    )

    assert (
        result.execution_side
        == ExecutionSide.BUY
    )


def test_risk_budget_limits_quantity():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.LONG,
            exposure=10000,
            max_loss=500,
        ),
        instrument=_instrument(),
        account_state=_account(
            max_loss=1000
        ),
        reference_price=100,
        fx_to_eur=1,
        stop_price=90,
    )

    # €10 risk per unit; €500 risk budget.
    assert result.quantity == 50

    assert (
        result.estimated_max_loss_eur
        == 500
    )


def test_target_exposure_limits_quantity():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.LONG,
            exposure=5000,
            max_loss=5000,
        ),
        instrument=_instrument(),
        account_state=_account(
            max_loss=5000
        ),
        reference_price=100,
        fx_to_eur=1,
        stop_price=90,
    )

    assert result.quantity == 50

    assert (
        result.gross_exposure_eur
        == 5000
    )


def test_margin_pct_reduces_capital_requirement():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.LONG,
            exposure=5000,
            max_loss=5000,
        ),
        instrument=_instrument(
            instrument_type=(
                InstrumentType.CFDC
            ),
            broker_leverage=5,
            margin_pct=20,
            settlement_currency=Currency.EUR,
            margin_currency=Currency.EUR,
        ),
        account_state=_account(),
        reference_price=100,
        fx_to_eur=1,
        stop_price=90,
    )

    assert (
        result.gross_exposure_eur
        == 5000
    )

    assert (
        result.estimated_margin_eur
        == 1000
    )

    assert (
        result.estimated_capital_required_eur
        == 1000
    )


def test_embedded_leverage_does_not_reduce_purchase_capital():

    instrument = _instrument(
        relationship=(
            ExposureRelationship.INVERSE
        ),
        instrument_type=(
            InstrumentType.ETF
        ),
        quote_currency=Currency.EUR,
        settlement_currency=Currency.EUR,
    )

    data = instrument.model_dump()

    data["embedded_leverage"] = 2

    instrument = (
        FinecoInstrument.model_validate(
            data
        )
    )

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.SHORT,
            exposure=5000,
            max_loss=5000,
        ),
        instrument=instrument,
        account_state=_account(),
        reference_price=100,
        fx_to_eur=1,
        stop_price=90,
    )

    assert (
        result.estimated_capital_required_eur
        == 5000
    )


def test_insufficient_cash_fails_constraint():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.LONG,
            exposure=5000,
            max_loss=5000,
        ),
        instrument=_instrument(
            quote_currency=Currency.EUR,
            settlement_currency=Currency.EUR,
        ),
        account_state=_account(
            eur=1000,
        ),
        reference_price=100,
        fx_to_eur=1,
        stop_price=90,
    )

    assert (
        result.constraints_passed
        is False
    )

    assert (
        "Insufficient deployable EUR cash."
        in result.violated_constraints
    )


# =============================================================
# CIO deployable-capital sizing
# =============================================================


def test_cio_deployable_limit_reduces_quantity_before_validation():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.SHORT,
            exposure=None,
            max_loss=5000,
        ),
        instrument=_instrument(
            quote_currency=Currency.USD,
            settlement_currency=Currency.USD,
        ),
        account_state=_account(
            eur=20000,
            eur_reserve=5000,
            max_loss=5000,
            max_cio_deployable_pct=80,
        ),
        reference_price=100,
        fx_to_eur=1,
        stop_price=110,
    )

    # Risk limit:
    #   €5,000 / €10 = 500 units.
    #
    # CIO deployable limit:
    #   (€20,000 - €5,000) * 80% = €12,000
    #   €12,000 / €100 = 120 units.
    #
    # Final quantity must be MIN(500, 120) = 120.
    assert result.quantity == 120
    assert result.gross_exposure_eur == 12000
    assert result.estimated_capital_required_eur == 12000
    assert result.constraints_passed is True


def test_cio_deployable_limit_respects_operator_reserve():

    with_reserve = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.SHORT,
            exposure=None,
            max_loss=5000,
        ),
        instrument=_instrument(),
        account_state=_account(
            eur=20000,
            eur_reserve=5000,
            max_loss=5000,
            max_cio_deployable_pct=80,
        ),
        reference_price=100,
        fx_to_eur=1,
        stop_price=110,
    )

    without_reserve = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.SHORT,
            exposure=None,
            max_loss=5000,
        ),
        instrument=_instrument(),
        account_state=_account(
            eur=20000,
            eur_reserve=0,
            max_loss=5000,
            max_cio_deployable_pct=80,
        ),
        reference_price=100,
        fx_to_eur=1,
        stop_price=110,
    )

    # With reserve:
    #   20,000 - 5,000 = 15,000 deployable
    #   80% = 12,000 -> 120 units.
    #
    # Without reserve:
    #   20,000 deployable
    #   80% = 16,000 -> 160 units.
    assert with_reserve.quantity == 120
    assert without_reserve.quantity == 160


def test_risk_budget_is_maximum_not_target_when_cio_cap_is_lower():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.SHORT,
            exposure=None,
            max_loss=5000,
        ),
        instrument=_instrument(),
        account_state=_account(
            eur=20000,
            eur_reserve=5000,
            max_loss=5000,
            max_cio_deployable_pct=80,
        ),
        reference_price=100,
        fx_to_eur=1,
        stop_price=110,
    )

    # CIO cap limits the trade to 120 units.
    # At €10 stop risk per unit the actual max loss is €1,200,
    # well below the €5,000 risk budget.
    assert result.quantity == 120
    assert result.risk_budget_eur == 5000
    assert result.estimated_max_loss_eur == 1200

    assert (
        result.estimated_max_loss_eur
        < result.risk_budget_eur
    )


def test_final_quantity_uses_risk_limit_when_risk_is_tighter_than_cio_cap():

    result = PositionSizer().size(
        opportunity=_opportunity(
            direction=Direction.SHORT,
            exposure=None,
            max_loss=500,
        ),
        instrument=_instrument(),
        account_state=_account(
            eur=100000,
            eur_reserve=0,
            max_loss=1000,
            max_cio_deployable_pct=80,
        ),
        reference_price=100,
        fx_to_eur=1,
        stop_price=110,
    )

    # Risk budget is min(opportunity 500, account 1000) = €500.
    # €500 / €10 risk per unit = 50 units.
    #
    # CIO capital cap is €80,000, so risk is the tighter limit.
    assert result.quantity == 50
    assert result.estimated_max_loss_eur == 500
    assert result.constraints_passed is True