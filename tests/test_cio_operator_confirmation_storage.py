from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.cio.models import (
    DataSource,
    OperatorConfirmation,
    OperatorConfirmationOutcome,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    25,
    21,
    30,
    tzinfo=timezone.utc,
)


def _confirmation(
    *,
    confirmation_id: str = "CONF-SMCI-001",
    execution_plan_id: str = "EXEC-SMCI-001",
    created_at: datetime = NOW,
    outcome: OperatorConfirmationOutcome = (
        OperatorConfirmationOutcome.EXECUTED
    ),
    executed_quantity: float | None = 429,
    executed_price: float | None = 37.18,
    commission_eur: float | None = 9.95,
    broker_order_reference: str | None = "FINECO-ORDER-123",
    notes: str | None = "Executed manually on Fineco.",
) -> OperatorConfirmation:

    if outcome == OperatorConfirmationOutcome.CANCELLED:
        executed_quantity = None
        executed_price = None
        commission_eur = None
        broker_order_reference = None

    return OperatorConfirmation(
        confirmation_id=confirmation_id,
        execution_plan_id=execution_plan_id,
        created_at=created_at,
        outcome=outcome,
        executed_quantity=executed_quantity,
        executed_price=executed_price,
        commission_eur=commission_eur,
        broker_order_reference=broker_order_reference,
        notes=notes,
        source=DataSource.OPERATOR,
    )


def _install_existing_execution_plan(
    monkeypatch,
    store: Stage3Store,
    execution_plan_id: str,
) -> None:
    """
    Stub the already-tested ExecutionPlan lookup so these tests focus
    only on OperatorConfirmation persistence semantics.
    """

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda requested_id: (
            SimpleNamespace(
                execution_plan_id=execution_plan_id,
            )
            if requested_id == execution_plan_id
            else None
        ),
    )


def test_operator_confirmation_save_and_get_round_trip(
    tmp_path,
    monkeypatch,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    confirmation = _confirmation()

    _install_existing_execution_plan(
        monkeypatch,
        store,
        confirmation.execution_plan_id,
    )

    store.save_operator_confirmation(
        confirmation
    )

    loaded = store.get_operator_confirmation(
        confirmation.confirmation_id
    )

    assert loaded is not None
    assert loaded == confirmation
    assert (
        loaded.outcome
        == OperatorConfirmationOutcome.EXECUTED
    )
    assert loaded.executed_quantity == 429
    assert loaded.executed_price == 37.18


def test_cancelled_confirmation_round_trip(
    tmp_path,
    monkeypatch,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    confirmation = _confirmation(
        confirmation_id="CONF-SMCI-CANCELLED",
        outcome=OperatorConfirmationOutcome.CANCELLED,
        notes="Operator cancelled before execution.",
    )

    _install_existing_execution_plan(
        monkeypatch,
        store,
        confirmation.execution_plan_id,
    )

    store.save_operator_confirmation(
        confirmation
    )

    loaded = store.get_operator_confirmation(
        confirmation.confirmation_id
    )

    assert loaded is not None
    assert (
        loaded.outcome
        == OperatorConfirmationOutcome.CANCELLED
    )
    assert loaded.executed_quantity is None
    assert loaded.executed_price is None
    assert loaded.commission_eur is None
    assert loaded.broker_order_reference is None


def test_operator_confirmation_upsert_updates_payload(
    tmp_path,
    monkeypatch,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    original = _confirmation()

    _install_existing_execution_plan(
        monkeypatch,
        store,
        original.execution_plan_id,
    )

    store.save_operator_confirmation(
        original
    )

    data = original.model_dump()
    data["notes"] = "Updated operator audit note."
    data["commission_eur"] = 12.50

    updated = OperatorConfirmation.model_validate(
        data
    )

    store.save_operator_confirmation(
        updated
    )

    loaded = store.get_operator_confirmation(
        original.confirmation_id
    )

    assert loaded is not None
    assert (
        loaded.notes
        == "Updated operator audit note."
    )
    assert loaded.commission_eur == 12.50


def test_operator_confirmation_list_is_newest_first(
    tmp_path,
    monkeypatch,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    older = _confirmation(
        confirmation_id="CONF-SMCI-OLD",
        created_at=NOW,
    )

    newer = _confirmation(
        confirmation_id="CONF-SMCI-NEW",
        created_at=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    _install_existing_execution_plan(
        monkeypatch,
        store,
        older.execution_plan_id,
    )

    store.save_operator_confirmation(
        older
    )

    store.save_operator_confirmation(
        newer
    )

    confirmations = (
        store
        .list_operator_confirmations_for_execution_plan(
            older.execution_plan_id
        )
    )

    assert [
        item.confirmation_id
        for item in confirmations
    ] == [
        "CONF-SMCI-NEW",
        "CONF-SMCI-OLD",
    ]


def test_latest_operator_confirmation_returns_newest(
    tmp_path,
    monkeypatch,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    older = _confirmation(
        confirmation_id="CONF-SMCI-OLD",
        created_at=NOW,
    )

    newer = _confirmation(
        confirmation_id="CONF-SMCI-NEW",
        created_at=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    _install_existing_execution_plan(
        monkeypatch,
        store,
        older.execution_plan_id,
    )

    store.save_operator_confirmation(
        older
    )

    store.save_operator_confirmation(
        newer
    )

    latest = (
        store
        .get_latest_operator_confirmation_for_execution_plan(
            older.execution_plan_id
        )
    )

    assert latest is not None
    assert (
        latest.confirmation_id
        == "CONF-SMCI-NEW"
    )


def test_missing_execution_plan_is_rejected(
    tmp_path,
    monkeypatch,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    confirmation = _confirmation()

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda _id: None,
    )

    with pytest.raises(
        ValueError,
        match="ExecutionPlan not found",
    ):
        store.save_operator_confirmation(
            confirmation
        )


def test_confirmation_is_scoped_to_execution_plan(
    tmp_path,
    monkeypatch,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    first = _confirmation(
        confirmation_id="CONF-SMCI-001",
        execution_plan_id="EXEC-SMCI-001",
    )

    second = _confirmation(
        confirmation_id="CONF-NVDA-001",
        execution_plan_id="EXEC-NVDA-001",
        executed_quantity=100,
        executed_price=180.0,
        broker_order_reference="FINECO-ORDER-456",
    )

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda requested_id: (
            SimpleNamespace(
                execution_plan_id=requested_id,
            )
            if requested_id in {
                "EXEC-SMCI-001",
                "EXEC-NVDA-001",
            }
            else None
        ),
    )

    store.save_operator_confirmation(
        first
    )

    store.save_operator_confirmation(
        second
    )

    smci = (
        store
        .list_operator_confirmations_for_execution_plan(
            "EXEC-SMCI-001"
        )
    )

    nvda = (
        store
        .list_operator_confirmations_for_execution_plan(
            "EXEC-NVDA-001"
        )
    )

    assert [
        item.confirmation_id
        for item in smci
    ] == [
        "CONF-SMCI-001"
    ]

    assert [
        item.confirmation_id
        for item in nvda
    ] == [
        "CONF-NVDA-001"
    ]


def test_storage_does_not_mutate_execution_plan_status(
    tmp_path,
    monkeypatch,
):

    store = Stage3Store(
        tmp_path / "cio.db"
    )

    confirmation = _confirmation()

    plan = SimpleNamespace(
        execution_plan_id=confirmation.execution_plan_id,
        status="WAITING_FOR_OPERATOR_CONFIRMATION",
    )

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda _id: plan,
    )

    # If storage tried to mutate/save the plan, this test would fail.
    monkeypatch.setattr(
        store,
        "save_execution_plan",
        lambda _plan: (_ for _ in ()).throw(
            AssertionError(
                "save_operator_confirmation must not mutate "
                "ExecutionPlan state"
            )
        ),
    )

    store.save_operator_confirmation(
        confirmation
    )

    assert (
        plan.status
        == "WAITING_FOR_OPERATOR_CONFIRMATION"
    )
