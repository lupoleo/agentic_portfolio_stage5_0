from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.cio.models import (
    Currency,
    Direction,
    ExecutionSide,
    TradeExitReason,
    TradeOutcome,
    TradeOutcomeStatus,
)


NOW = datetime(
    2026,
    8,
    27,
    19,
    0,
    tzinfo=timezone.utc,
)


def _outcome_data() -> dict:

    return {
        "outcome_id": "OUT-TEST-001",
        "created_at": NOW,
        "updated_at": NOW,
        "opportunity_id": "OPP-TEST",
        "proposal_id": "PROP-TEST",
        "simulation_id": "SIM-TEST",
        "decision_id": "DEC-TEST",
        "execution_plan_id": "EXEC-TEST",
        "confirmation_id": "CONF-TEST",
        "snapshot_id": "SNAP-TEST",
        "ticker": "TEST",
        "instrument_id": "FIN-TEST",
        "direction": Direction.LONG,
        "execution_side": ExecutionSide.BUY,
        "currency": Currency.USD,
        "entry_datetime": NOW,
        "entry_quantity": 10,
        "entry_price": 100.0,
        "entry_commission_eur": 4.95,
        "planned_reference_price": 99.5,
        "planned_stop_price": 95.0,
        "planned_target_1": 110.0,
        "planned_target_2": 115.0,
        "planned_max_loss_eur": 45.0,
        "expected_holding_min_days": 2,
        "expected_holding_max_days": 5,
        "status": TradeOutcomeStatus.OPEN,
        "notes": "Model contract test.",
    }


def test_open_trade_outcome_is_valid():

    outcome = TradeOutcome.model_validate(
        _outcome_data()
    )

    assert outcome.status == TradeOutcomeStatus.OPEN
    assert outcome.entry_quantity == 10
    assert outcome.entry_price == 100.0
    assert outcome.exit_price is None
    assert outcome.realized_pnl_eur is None


def test_trade_outcome_supports_short_trade():

    data = _outcome_data()
    data["direction"] = Direction.SHORT
    data["execution_side"] = ExecutionSide.SELL_SHORT

    outcome = TradeOutcome.model_validate(
        data
    )

    assert outcome.direction == Direction.SHORT
    assert outcome.execution_side == ExecutionSide.SELL_SHORT


def test_entry_quantity_must_be_positive():

    data = _outcome_data()
    data["entry_quantity"] = 0

    with pytest.raises(ValidationError):
        TradeOutcome.model_validate(
            data
        )


def test_entry_price_must_be_positive():

    data = _outcome_data()
    data["entry_price"] = 0

    with pytest.raises(ValidationError):
        TradeOutcome.model_validate(
            data
        )


def test_updated_at_cannot_precede_created_at():

    data = _outcome_data()
    data["updated_at"] = (
        NOW - timedelta(seconds=1)
    )

    with pytest.raises(
        ValidationError,
        match="updated_at cannot be earlier",
    ):
        TradeOutcome.model_validate(
            data
        )


def test_expected_holding_range_must_be_ordered():

    data = _outcome_data()
    data["expected_holding_min_days"] = 6
    data["expected_holding_max_days"] = 5

    with pytest.raises(
        ValidationError,
        match="expected_holding_min_days cannot exceed",
    ):
        TradeOutcome.model_validate(
            data
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "exit_datetime",
            NOW + timedelta(days=1),
        ),
        (
            "exit_quantity",
            10,
        ),
        (
            "exit_price",
            105.0,
        ),
        (
            "exit_commission_eur",
            4.95,
        ),
        (
            "exit_reason",
            TradeExitReason.MANUAL,
        ),
        (
            "realized_pnl_eur",
            50.0,
        ),
        (
            "realized_return_pct",
            5.0,
        ),
        (
            "holding_days",
            1.0,
        ),
    ],
)
def test_open_outcome_rejects_realized_exit_fields(
    field,
    value,
):

    data = _outcome_data()
    data[field] = value

    with pytest.raises(
        ValidationError,
        match="OPEN TradeOutcome cannot contain",
    ):
        TradeOutcome.model_validate(
            data
        )


def test_closed_trade_outcome_is_valid():

    data = _outcome_data()

    data.update(
        {
            "updated_at": NOW + timedelta(days=3),
            "entry_fx_to_eur": 0.86,
            "exit_datetime": NOW + timedelta(days=3),
            "exit_quantity": 10,
            "exit_price": 110.0,
            "exit_commission_eur": 4.95,
            "exit_fx_to_eur": 0.87,
            "exit_reason": TradeExitReason.TARGET_1,
            "realized_pnl_eur": 990.10,
            "realized_return_pct": 9.901,
            "holding_days": 3.0,
            "status": TradeOutcomeStatus.CLOSED,
        }
    )

    outcome = TradeOutcome.model_validate(
        data
    )

    assert outcome.status == TradeOutcomeStatus.CLOSED
    assert outcome.exit_reason == TradeExitReason.TARGET_1
    assert outcome.realized_pnl_eur == 990.10


@pytest.mark.parametrize(
    "missing_field",
    [
        "exit_datetime",
        "exit_quantity",
        "exit_price",
        "exit_reason",
    ],
)
def test_closed_outcome_requires_core_exit_fields(
    missing_field,
):

    data = _outcome_data()

    data.update(
        {
            "updated_at": NOW + timedelta(days=3),
            "exit_datetime": NOW + timedelta(days=3),
            "exit_quantity": 10,
            "exit_price": 110.0,
            "exit_reason": TradeExitReason.MANUAL,
            "status": TradeOutcomeStatus.CLOSED,
        }
    )

    data[missing_field] = None

    with pytest.raises(
        ValidationError,
        match="CLOSED TradeOutcome requires",
    ):
        TradeOutcome.model_validate(
            data
        )


def test_exit_datetime_cannot_precede_entry_datetime():

    data = _outcome_data()

    data.update(
        {
            "exit_datetime": NOW - timedelta(seconds=1),
            "exit_quantity": 10,
            "exit_price": 105.0,
            "exit_reason": TradeExitReason.MANUAL,
            "status": TradeOutcomeStatus.CLOSED,
        }
    )

    with pytest.raises(
        ValidationError,
        match="exit_datetime cannot be earlier",
    ):
        TradeOutcome.model_validate(
            data
        )


def test_closed_trade_requires_full_exit_quantity():

    data = _outcome_data()

    data.update(
        {
            "updated_at": NOW + timedelta(days=1),
            "exit_datetime": NOW + timedelta(days=1),
            "exit_quantity": 11,
            "exit_price": 105.0,
            "exit_reason": TradeExitReason.MANUAL,
            "status": TradeOutcomeStatus.CLOSED,
        }
    )

    with pytest.raises(
        ValidationError,
        match="exit_quantity must equal entry_quantity",
    ):
        TradeOutcome.model_validate(
            data
        )


def test_negative_commission_is_rejected():

    data = _outcome_data()
    data["entry_commission_eur"] = -0.01

    with pytest.raises(ValidationError):
        TradeOutcome.model_validate(
            data
        )


def test_all_exit_reasons_are_supported():

    expected = {
        "TARGET_1",
        "TARGET_2",
        "STOP",
        "MANUAL",
        "TIME_EXIT",
        "THESIS_INVALIDATED",
        "EVENT_COMPLETED",
        "OTHER",
    }

    assert {
        item.value
        for item in TradeExitReason
    } == expected
