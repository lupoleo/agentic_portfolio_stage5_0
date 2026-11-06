from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.cio.models import (
    DataSource,
    OperatorConfirmation,
    OperatorConfirmationOutcome,
)


NOW = datetime(
    2026,
    8,
    25,
    21,
    15,
    tzinfo=timezone.utc,
)


def _base_kwargs() -> dict:
    return {
        "confirmation_id": "CONF-SMCI-001",
        "execution_plan_id": "EXEC-SMCI-001",
        "created_at": NOW,
    }


def test_executed_confirmation_is_valid():

    confirmation = OperatorConfirmation(
        **_base_kwargs(),
        outcome=OperatorConfirmationOutcome.EXECUTED,
        executed_quantity=429,
        executed_price=37.18,
        commission_eur=9.95,
        broker_order_reference="FINECO-ORDER-123",
        notes="Executed manually on Fineco.",
    )

    assert (
        confirmation.outcome
        == OperatorConfirmationOutcome.EXECUTED
    )

    assert confirmation.executed_quantity == 429
    assert confirmation.executed_price == 37.18
    assert confirmation.commission_eur == 9.95
    assert (
        confirmation.broker_order_reference
        == "FINECO-ORDER-123"
    )
    assert confirmation.source == DataSource.OPERATOR


def test_executed_requires_quantity():

    with pytest.raises(
        ValidationError,
        match="executed_quantity is required",
    ):
        OperatorConfirmation(
            **_base_kwargs(),
            outcome=OperatorConfirmationOutcome.EXECUTED,
            executed_quantity=None,
            executed_price=37.18,
        )


def test_executed_requires_price():

    with pytest.raises(
        ValidationError,
        match="executed_price is required",
    ):
        OperatorConfirmation(
            **_base_kwargs(),
            outcome=OperatorConfirmationOutcome.EXECUTED,
            executed_quantity=429,
            executed_price=None,
        )


def test_cancelled_confirmation_is_valid():

    confirmation = OperatorConfirmation(
        **_base_kwargs(),
        outcome=OperatorConfirmationOutcome.CANCELLED,
        notes="Operator decided not to execute.",
    )

    assert (
        confirmation.outcome
        == OperatorConfirmationOutcome.CANCELLED
    )

    assert confirmation.executed_quantity is None
    assert confirmation.executed_price is None
    assert confirmation.commission_eur is None
    assert confirmation.broker_order_reference is None
    assert confirmation.source == DataSource.OPERATOR


@pytest.mark.parametrize(
    (
        "field_name",
        "field_value",
        "message",
    ),
    [
        (
            "executed_quantity",
            100,
            "executed_quantity must be None",
        ),
        (
            "executed_price",
            37.18,
            "executed_price must be None",
        ),
        (
            "commission_eur",
            9.95,
            "commission_eur must be None",
        ),
        (
            "broker_order_reference",
            "FINECO-ORDER-123",
            "broker_order_reference must be None",
        ),
    ],
)
def test_cancelled_rejects_execution_fields(
    field_name,
    field_value,
    message,
):

    kwargs = _base_kwargs()

    kwargs.update(
        {
            "outcome":
                OperatorConfirmationOutcome.CANCELLED,

            field_name:
                field_value,
        }
    )

    with pytest.raises(
        ValidationError,
        match=message,
    ):
        OperatorConfirmation(
            **kwargs
        )


def test_source_defaults_to_operator():

    confirmation = OperatorConfirmation(
        **_base_kwargs(),
        outcome=OperatorConfirmationOutcome.EXECUTED,
        executed_quantity=429,
        executed_price=37.18,
    )

    assert (
        confirmation.source
        == DataSource.OPERATOR
    )
