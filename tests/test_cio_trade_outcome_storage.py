from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.cio.models import (
    Currency,
    Direction,
    ExecutionSide,
    TradeExitReason,
    TradeOutcome,
    TradeOutcomeStatus,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    27,
    21,
    0,
    tzinfo=timezone.utc,
)


def _plan():
    return SimpleNamespace(
        execution_plan_id="EXEC-TEST-001",
        opportunity_id="OPP-TEST-001",
        proposal_id="PROP-TEST-001",
        simulation_id="SIM-TEST-001",
        decision_id="DEC-TEST-001",
        snapshot_id="SNAP-TEST-001",
        instrument_id="FIN-TEST-001",
        direction=Direction.LONG,
        execution_side=ExecutionSide.BUY,
        currency=Currency.USD,
    )


def _confirmation():
    return SimpleNamespace(
        confirmation_id="CONF-TEST-001",
        execution_plan_id="EXEC-TEST-001",
        created_at=NOW,
        executed_quantity=10,
        executed_price=101.25,
        commission_eur=4.95,
    )


def _outcome(
    *,
    outcome_id: str = "OUT-TEST-001",
    opportunity_id: str = "OPP-TEST-001",
    execution_plan_id: str = "EXEC-TEST-001",
    confirmation_id: str = "CONF-TEST-001",
    instrument_id: str = "FIN-TEST-001",
    status: TradeOutcomeStatus = TradeOutcomeStatus.OPEN,
    updated_at: datetime = NOW,
) -> TradeOutcome:

    data = dict(
        outcome_id=outcome_id,
        created_at=NOW,
        updated_at=updated_at,
        opportunity_id=opportunity_id,
        proposal_id="PROP-TEST-001",
        simulation_id="SIM-TEST-001",
        decision_id="DEC-TEST-001",
        execution_plan_id=execution_plan_id,
        confirmation_id=confirmation_id,
        snapshot_id="SNAP-TEST-001",
        ticker="TEST",
        instrument_id=instrument_id,
        direction=Direction.LONG,
        execution_side=ExecutionSide.BUY,
        currency=Currency.USD,
        entry_datetime=NOW,
        entry_quantity=10,
        entry_price=101.25,
        entry_commission_eur=4.95,
        planned_reference_price=100.0,
        planned_stop_price=95.0,
        planned_target_1=110.0,
        planned_target_2=115.0,
        planned_max_loss_eur=43.0,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        status=status,
        notes="Storage test.",
    )

    if status == TradeOutcomeStatus.CLOSED:
        data.update(
            {
                "entry_fx_to_eur":
                    0.86,

                "exit_datetime":
                    NOW + timedelta(days=2),

                "exit_quantity":
                    10,

                "exit_price":
                    110.0,

                "exit_commission_eur":
                    4.95,

                "exit_fx_to_eur":
                    0.87,

                "exit_reason":
                    TradeExitReason.TARGET_1,

                "realized_pnl_eur":
                    70.0,

                "realized_return_pct":
                    8.0,

                "holding_days":
                    2.0,
            }
        )

    return TradeOutcome.model_validate(
        data
    )


def _install_valid_chain(
    monkeypatch,
    store: Stage3Store,
):
    plan = _plan()
    confirmation = _confirmation()

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda _id: plan,
    )

    monkeypatch.setattr(
        store,
        "get_operator_confirmation",
        lambda _id: confirmation,
    )


def test_trade_outcome_save_and_get_round_trip(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    _install_valid_chain(
        monkeypatch,
        store,
    )

    outcome = _outcome()

    store.save_trade_outcome(
        outcome
    )

    loaded = store.get_trade_outcome(
        outcome.outcome_id
    )

    assert loaded == outcome


def test_trade_outcome_for_execution_plan_lookup(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    _install_valid_chain(
        monkeypatch,
        store,
    )

    outcome = _outcome()

    store.save_trade_outcome(
        outcome
    )

    loaded = (
        store
        .get_trade_outcome_for_execution_plan(
            outcome.execution_plan_id
        )
    )

    assert loaded == outcome


def test_trade_outcome_upsert_open_to_closed(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    _install_valid_chain(
        monkeypatch,
        store,
    )

    open_outcome = _outcome()

    store.save_trade_outcome(
        open_outcome
    )

    closed_outcome = _outcome(
        status=TradeOutcomeStatus.CLOSED,
        updated_at=(
            NOW
            + timedelta(days=2)
        ),
    )

    store.save_trade_outcome(
        closed_outcome
    )

    loaded = store.get_trade_outcome(
        open_outcome.outcome_id
    )

    assert loaded is not None
    assert loaded.status == TradeOutcomeStatus.CLOSED
    assert loaded.exit_reason == TradeExitReason.TARGET_1


def test_trade_outcomes_are_listed_by_updated_at_desc(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    _install_valid_chain(
        monkeypatch,
        store,
    )

    older = _outcome(
        outcome_id="OUT-OLD",
        execution_plan_id="EXEC-OLD",
        confirmation_id="CONF-OLD",
        updated_at=NOW,
    )

    newer = _outcome(
        outcome_id="OUT-NEW",
        execution_plan_id="EXEC-NEW",
        confirmation_id="CONF-NEW",
        updated_at=(
            NOW
            + timedelta(hours=1)
        ),
    )

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda execution_plan_id: (
            SimpleNamespace(
                execution_plan_id=execution_plan_id,
                opportunity_id="OPP-TEST-001",
                proposal_id="PROP-TEST-001",
                simulation_id="SIM-TEST-001",
                decision_id="DEC-TEST-001",
                snapshot_id="SNAP-TEST-001",
                instrument_id="FIN-TEST-001",
                direction=Direction.LONG,
                execution_side=ExecutionSide.BUY,
                currency=Currency.USD,
            )
        ),
    )

    monkeypatch.setattr(
        store,
        "get_operator_confirmation",
        lambda confirmation_id: (
            SimpleNamespace(
                confirmation_id=confirmation_id,
                execution_plan_id=(
                    "EXEC-OLD"
                    if confirmation_id == "CONF-OLD"
                    else "EXEC-NEW"
                ),
                created_at=NOW,
                executed_quantity=10,
                executed_price=101.25,
                commission_eur=4.95,
            )
        ),
    )

    store.save_trade_outcome(
        older
    )

    store.save_trade_outcome(
        newer
    )

    items = store.list_trade_outcomes(
        "OPP-TEST-001"
    )

    assert [
        item.outcome_id
        for item in items
    ] == [
        "OUT-NEW",
        "OUT-OLD",
    ]


def test_latest_trade_outcome_returns_most_recent(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    _install_valid_chain(
        monkeypatch,
        store,
    )

    outcome = _outcome()

    store.save_trade_outcome(
        outcome
    )

    latest = store.get_latest_trade_outcome(
        outcome.opportunity_id
    )

    assert latest == outcome


def test_missing_execution_plan_is_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda _id: None,
    )

    with pytest.raises(
        ValueError,
        match="ExecutionPlan not found",
    ):
        store.save_trade_outcome(
            _outcome()
        )


def test_missing_confirmation_is_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda _id: _plan(),
    )

    monkeypatch.setattr(
        store,
        "get_operator_confirmation",
        lambda _id: None,
    )

    with pytest.raises(
        ValueError,
        match="OperatorConfirmation not found",
    ):
        store.save_trade_outcome(
            _outcome()
        )


def test_confirmation_must_belong_to_execution_plan(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    monkeypatch.setattr(
        store,
        "get_execution_plan",
        lambda _id: _plan(),
    )

    monkeypatch.setattr(
        store,
        "get_operator_confirmation",
        lambda _id: SimpleNamespace(
            confirmation_id="CONF-TEST-001",
            execution_plan_id="EXEC-OTHER",
            created_at=NOW,
            executed_quantity=10,
            executed_price=101.25,
            commission_eur=4.95,
        ),
    )

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):
        store.save_trade_outcome(
            _outcome()
        )


@pytest.mark.parametrize(
    (
        "field_name",
        "wrong_value",
    ),
    [
        (
            "opportunity_id",
            "OPP-OTHER",
        ),
        (
            "proposal_id",
            "PROP-OTHER",
        ),
        (
            "simulation_id",
            "SIM-OTHER",
        ),
        (
            "decision_id",
            "DEC-OTHER",
        ),
        (
            "snapshot_id",
            "SNAP-OTHER",
        ),
        (
            "instrument_id",
            "FIN-OTHER",
        ),
        (
            "direction",
            Direction.SHORT,
        ),
        (
            "execution_side",
            ExecutionSide.SELL_SHORT,
        ),
        (
            "currency",
            Currency.EUR,
        ),
    ],
)
def test_outcome_provenance_must_match_execution_plan(
    tmp_path,
    monkeypatch,
    field_name,
    wrong_value,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    _install_valid_chain(
        monkeypatch,
        store,
    )

    data = _outcome().model_dump()
    data[field_name] = wrong_value

    outcome = TradeOutcome.model_validate(
        data
    )

    with pytest.raises(
        ValueError,
        match=(
            f"TradeOutcome {field_name} does not match "
            "ExecutionPlan"
        ),
    ):
        store.save_trade_outcome(
            outcome
        )


@pytest.mark.parametrize(
    (
        "field_name",
        "wrong_value",
        "message",
    ),
    [
        (
            "entry_datetime",
            NOW + timedelta(seconds=1),
            "entry_datetime does not match",
        ),
        (
            "entry_quantity",
            9,
            "entry_quantity does not match",
        ),
        (
            "entry_price",
            100.0,
            "entry_price does not match",
        ),
        (
            "entry_commission_eur",
            5.95,
            "entry_commission_eur does not match",
        ),
    ],
)
def test_entry_facts_must_match_operator_confirmation(
    tmp_path,
    monkeypatch,
    field_name,
    wrong_value,
    message,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    _install_valid_chain(
        monkeypatch,
        store,
    )

    data = _outcome().model_dump()
    data[field_name] = wrong_value

    outcome = TradeOutcome.model_validate(
        data
    )

    with pytest.raises(
        ValueError,
        match=message,
    ):
        store.save_trade_outcome(
            outcome
        )


def test_execution_plan_can_have_only_one_outcome_id(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    _install_valid_chain(
        monkeypatch,
        store,
    )

    first = _outcome(
        outcome_id="OUT-FIRST"
    )

    second = _outcome(
        outcome_id="OUT-SECOND"
    )

    store.save_trade_outcome(
        first
    )

    with pytest.raises(
        ValueError,
        match="already has a different TradeOutcome",
    ):
        store.save_trade_outcome(
            second
        )
