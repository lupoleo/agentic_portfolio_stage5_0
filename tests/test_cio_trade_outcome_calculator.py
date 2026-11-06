from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.cio.models import (
    Currency,
    Direction,
    ExecutionSide,
    TradeOutcome,
    TradeOutcomeStatus,
)
from app.cio.trade_outcome_calculator import (
    TradeOutcomeCalculator,
)


NOW = datetime(
    2026,
    8,
    27,
    20,
    0,
    tzinfo=timezone.utc,
)


def _outcome(
    *,
    direction: Direction = Direction.LONG,
    currency: Currency = Currency.USD,
    entry_price: float = 100.0,
    reference_price: float | None = 99.0,
    entry_fx_to_eur: float | None = 0.86,
    entry_commission_eur: float | None = 5.0,
    status: TradeOutcomeStatus = TradeOutcomeStatus.OPEN,
) -> TradeOutcome:

    execution_side = (
        ExecutionSide.BUY
        if direction == Direction.LONG
        else ExecutionSide.SELL_SHORT
    )

    data = dict(
        outcome_id="OUT-CALC-001",
        created_at=NOW,
        updated_at=NOW,
        opportunity_id="OPP-CALC-001",
        proposal_id="PROP-CALC-001",
        simulation_id="SIM-CALC-001",
        decision_id="DEC-CALC-001",
        execution_plan_id="EXEC-CALC-001",
        confirmation_id="CONF-CALC-001",
        snapshot_id="SNAP-CALC-001",
        ticker="TEST",
        instrument_id="FIN-CALC-001",
        direction=direction,
        execution_side=execution_side,
        currency=currency,
        entry_datetime=NOW,
        entry_quantity=10,
        entry_price=entry_price,
        entry_commission_eur=entry_commission_eur,
        entry_fx_to_eur=(
            None
            if currency == Currency.EUR
            else entry_fx_to_eur
        ),
        planned_reference_price=reference_price,
        planned_stop_price=95.0,
        planned_target_1=110.0,
        planned_target_2=115.0,
        planned_max_loss_eur=45.0,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        status=status,
    )

    return TradeOutcome.model_validate(
        data
    )


def test_long_usd_realized_pnl_uses_entry_and_exit_fx():

    result = TradeOutcomeCalculator().calculate_closed(
        outcome=_outcome(),
        exit_datetime=NOW + timedelta(days=2),
        exit_price=110.0,
        exit_quantity=10,
        exit_commission_eur=5.0,
        exit_fx_to_eur=0.87,
    )

    # Entry = 100 * 10 * .86 = 860
    # Exit  = 110 * 10 * .87 = 957
    # Gross = 97; commissions = 10; net = 87
    assert result.realized_pnl_eur == pytest.approx(87.0)
    assert result.realized_return_pct == pytest.approx(
        87.0 / 860.0 * 100.0
    )
    assert result.holding_days == pytest.approx(2.0)


def test_short_usd_realized_pnl_is_inverted():

    result = TradeOutcomeCalculator().calculate_closed(
        outcome=_outcome(
            direction=Direction.SHORT,
            reference_price=101.0,
        ),
        exit_datetime=NOW + timedelta(days=1),
        exit_price=90.0,
        exit_quantity=10,
        exit_commission_eur=5.0,
        exit_fx_to_eur=0.87,
    )

    # Entry = 860; exit = 783; gross short P/L = 77; net = 67.
    assert result.realized_pnl_eur == pytest.approx(67.0)


def test_eur_trade_does_not_require_fx():

    result = TradeOutcomeCalculator().calculate_closed(
        outcome=_outcome(
            currency=Currency.EUR,
        ),
        exit_datetime=NOW + timedelta(hours=12),
        exit_price=105.0,
        exit_quantity=10,
        exit_commission_eur=5.0,
    )

    # 50 gross - 10 commissions.
    assert result.realized_pnl_eur == pytest.approx(40.0)
    assert result.holding_days == pytest.approx(0.5)


def test_long_adverse_entry_slippage_is_positive():

    result = TradeOutcomeCalculator().calculate_closed(
        outcome=_outcome(
            entry_price=101.0,
            reference_price=100.0,
        ),
        exit_datetime=NOW + timedelta(days=1),
        exit_price=105.0,
        exit_quantity=10,
        exit_fx_to_eur=0.86,
    )

    assert result.entry_slippage_pct == pytest.approx(1.0)


def test_short_adverse_entry_slippage_is_positive():

    result = TradeOutcomeCalculator().calculate_closed(
        outcome=_outcome(
            direction=Direction.SHORT,
            entry_price=99.0,
            reference_price=100.0,
        ),
        exit_datetime=NOW + timedelta(days=1),
        exit_price=95.0,
        exit_quantity=10,
        exit_fx_to_eur=0.86,
    )

    assert result.entry_slippage_pct == pytest.approx(1.0)


def test_missing_reference_price_produces_no_slippage():

    result = TradeOutcomeCalculator().calculate_closed(
        outcome=_outcome(
            reference_price=None,
        ),
        exit_datetime=NOW + timedelta(days=1),
        exit_price=105.0,
        exit_quantity=10,
        exit_fx_to_eur=0.86,
    )

    assert result.entry_slippage_pct is None


def test_missing_entry_fx_is_rejected_for_usd():

    outcome = _outcome(
        entry_fx_to_eur=None,
    )

    with pytest.raises(
        ValueError,
        match="entry_fx_to_eur is required",
    ):
        TradeOutcomeCalculator().calculate_closed(
            outcome=outcome,
            exit_datetime=NOW + timedelta(days=1),
            exit_price=105.0,
            exit_quantity=10,
            exit_fx_to_eur=0.86,
        )


def test_missing_exit_fx_is_rejected_for_usd():

    with pytest.raises(
        ValueError,
        match="exit_fx_to_eur is required",
    ):
        TradeOutcomeCalculator().calculate_closed(
            outcome=_outcome(),
            exit_datetime=NOW + timedelta(days=1),
            exit_price=105.0,
            exit_quantity=10,
        )


def test_partial_close_is_rejected():

    with pytest.raises(
        ValueError,
        match="Full-close V1",
    ):
        TradeOutcomeCalculator().calculate_closed(
            outcome=_outcome(),
            exit_datetime=NOW + timedelta(days=1),
            exit_price=105.0,
            exit_quantity=5,
            exit_fx_to_eur=0.86,
        )


def test_exit_before_entry_is_rejected():

    with pytest.raises(
        ValueError,
        match="exit_datetime cannot be earlier",
    ):
        TradeOutcomeCalculator().calculate_closed(
            outcome=_outcome(),
            exit_datetime=NOW - timedelta(seconds=1),
            exit_price=105.0,
            exit_quantity=10,
            exit_fx_to_eur=0.86,
        )


@pytest.mark.parametrize(
    "exit_price",
    [
        0.0,
        -1.0,
    ],
)
def test_non_positive_exit_price_is_rejected(
    exit_price,
):

    with pytest.raises(
        ValueError,
        match="exit_price must be greater than zero",
    ):
        TradeOutcomeCalculator().calculate_closed(
            outcome=_outcome(),
            exit_datetime=NOW + timedelta(days=1),
            exit_price=exit_price,
            exit_quantity=10,
            exit_fx_to_eur=0.86,
        )


def test_negative_exit_commission_is_rejected():

    with pytest.raises(
        ValueError,
        match="exit_commission_eur cannot be negative",
    ):
        TradeOutcomeCalculator().calculate_closed(
            outcome=_outcome(),
            exit_datetime=NOW + timedelta(days=1),
            exit_price=105.0,
            exit_quantity=10,
            exit_commission_eur=-0.01,
            exit_fx_to_eur=0.86,
        )
