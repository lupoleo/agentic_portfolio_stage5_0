from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import app.cio.cli as cli_module
from app.cio.models import (
    Currency,
    Direction,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionSide,
    OperatorConfirmation,
    OperatorConfirmationOutcome,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    27,
    19,
    0,
    tzinfo=timezone.utc,
)

OPPORTUNITY_ID = "OPP-TEST-CLI"
EXECUTION_PLAN_ID = "EXEC-TEST-CLI"


def _plan(
    *,
    status: ExecutionPlanStatus = (
        ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
    ),
) -> ExecutionPlan:

    return ExecutionPlan(
        execution_plan_id=EXECUTION_PLAN_ID,
        created_at=NOW,
        opportunity_id=OPPORTUNITY_ID,
        proposal_id="PROP-TEST-CLI",
        simulation_id="SIM-TEST-CLI",
        decision_id="DEC-TEST-CLI",
        snapshot_id="SNAP-TEST-CLI",
        broker="Fineco",
        underlying="TEST",
        instrument_id="FIN-TEST-CLI",
        instrument_description="Test Ordinary NASDAQ",
        broker_symbol="TEST",
        market="NASDAQ",
        currency=Currency.USD,
        direction=Direction.LONG,
        execution_side=ExecutionSide.BUY,
        quantity=10,
        order_type="MARKET",
        reference_price=100.0,
        entry_price=None,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        fx_to_eur=0.86,
        gross_exposure_eur=860.0,
        estimated_capital_required_eur=860.0,
        estimated_max_loss_eur=43.0,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        status=status,
        execution_notes="CLI test plan.",
    )


def _confirmation(
    *,
    outcome: OperatorConfirmationOutcome = (
        OperatorConfirmationOutcome.EXECUTED
    ),
) -> OperatorConfirmation:

    if outcome == OperatorConfirmationOutcome.EXECUTED:
        return OperatorConfirmation(
            confirmation_id="CONF-TEST-CLI",
            execution_plan_id=EXECUTION_PLAN_ID,
            created_at=NOW,
            outcome=outcome,
            executed_quantity=10,
            executed_price=101.25,
            commission_eur=4.95,
            broker_order_reference="BROKER-REF-001",
            notes="CLI test execution.",
        )

    return OperatorConfirmation(
        confirmation_id="CONF-TEST-CLI-CANCEL",
        execution_plan_id=EXECUTION_PLAN_ID,
        created_at=NOW,
        outcome=outcome,
        notes="CLI test cancellation.",
    )


def _patch_store(
    monkeypatch,
    store: Mock,
) -> None:

    monkeypatch.setattr(
        cli_module,
        "Stage3Store",
        lambda _db_path: store,
    )


def _feed_inputs(
    monkeypatch,
    values: list[str],
) -> None:

    iterator = iter(values)

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt="": next(iterator),
    )


def test_cli_confirm_executed_records_confirmation_and_transition(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    waiting_plan = _plan()

    confirmed_plan = _plan(
        status=ExecutionPlanStatus.OPERATOR_CONFIRMED
    )

    store.get_latest_execution_plan.return_value = (
        waiting_plan
    )

    store.get_execution_plan.side_effect = [
        waiting_plan,
        confirmed_plan,
    ]

    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        None
    )

    _patch_store(
        monkeypatch,
        store,
    )

    _feed_inputs(
        monkeypatch,
        [
            "EXECUTED",
            "10",
            "101.25",
            "4.95",
            "BROKER-REF-001",
            "Executed manually in CLI test.",
        ],
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "confirm",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert "OPERATOR CONFIRMATION" in out
    assert "Outcome:          EXECUTED" in out
    assert "Executed quantity: 10" in out
    assert "Executed price:   101.25" in out
    assert "Commission EUR:   €4.95" in out
    assert "Broker reference: BROKER-REF-001" in out

    assert (
        "Previous status: "
        "WAITING_FOR_OPERATOR_CONFIRMATION"
        in out
    )

    assert (
        "Current status:  OPERATOR_CONFIRMED"
        in out
    )

    store.save_operator_confirmation.assert_called_once()

    saved_confirmation = (
        store.save_operator_confirmation.call_args.args[0]
    )

    assert (
        saved_confirmation.outcome
        == OperatorConfirmationOutcome.EXECUTED
    )

    assert saved_confirmation.executed_quantity == 10
    assert saved_confirmation.executed_price == 101.25

    store.save_execution_plan.assert_called_once()

    saved_plan = (
        store.save_execution_plan.call_args.args[0]
    )

    assert (
        saved_plan.status
        == ExecutionPlanStatus.OPERATOR_CONFIRMED
    )


def test_cli_confirm_cancelled_records_confirmation_and_transition(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    waiting_plan = _plan()

    cancelled_plan = _plan(
        status=ExecutionPlanStatus.CANCELLED
    )

    store.get_latest_execution_plan.return_value = (
        waiting_plan
    )

    store.get_execution_plan.side_effect = [
        waiting_plan,
        cancelled_plan,
    ]

    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        None
    )

    _patch_store(
        monkeypatch,
        store,
    )

    _feed_inputs(
        monkeypatch,
        [
            "CANCELLED",
            "Cancelled manually in CLI test.",
        ],
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "confirm",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert "Outcome:          CANCELLED" in out

    assert (
        "Previous status: "
        "WAITING_FOR_OPERATOR_CONFIRMATION"
        in out
    )

    assert (
        "Current status:  CANCELLED"
        in out
    )

    store.save_operator_confirmation.assert_called_once()

    saved_confirmation = (
        store.save_operator_confirmation.call_args.args[0]
    )

    assert (
        saved_confirmation.outcome
        == OperatorConfirmationOutcome.CANCELLED
    )

    assert saved_confirmation.executed_quantity is None
    assert saved_confirmation.executed_price is None

    store.save_execution_plan.assert_called_once()

    saved_plan = (
        store.save_execution_plan.call_args.args[0]
    )

    assert (
        saved_plan.status
        == ExecutionPlanStatus.CANCELLED
    )


def test_cli_confirmation_shows_latest_persisted_confirmation(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    plan = _plan(
        status=ExecutionPlanStatus.OPERATOR_CONFIRMED
    )

    confirmation = _confirmation()

    store.get_latest_execution_plan.return_value = plan

    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        confirmation
    )

    _patch_store(
        monkeypatch,
        store,
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "confirmation",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert "CIO OPERATOR CONFIRMATION" in out
    assert "CONF-TEST-CLI" in out
    assert "Outcome:          EXECUTED" in out
    assert "Executed quantity: 10" in out
    assert "Executed price:   101.25" in out

    store.get_latest_operator_confirmation_for_execution_plan.assert_called_once_with(
        EXECUTION_PLAN_ID
    )

    store.save_operator_confirmation.assert_not_called()
    store.save_execution_plan.assert_not_called()


def test_cli_confirm_reports_missing_execution_plan(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    store.get_latest_execution_plan.return_value = None

    _patch_store(
        monkeypatch,
        store,
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "confirm",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert (
        "No persisted ExecutionPlan found for "
        f"{OPPORTUNITY_ID}."
        in out
    )

    store.save_operator_confirmation.assert_not_called()
    store.save_execution_plan.assert_not_called()


def test_cli_confirmation_reports_missing_execution_plan(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    store.get_latest_execution_plan.return_value = None

    _patch_store(
        monkeypatch,
        store,
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "confirmation",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert (
        "No persisted ExecutionPlan found for "
        f"{OPPORTUNITY_ID}."
        in out
    )


def test_cli_confirmation_reports_missing_confirmation(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    plan = _plan()

    store.get_latest_execution_plan.return_value = plan

    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        None
    )

    _patch_store(
        monkeypatch,
        store,
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "confirmation",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert (
        "No persisted OperatorConfirmation found for "
        f"{EXECUTION_PLAN_ID}."
        in out
    )


def test_cli_confirm_surfaces_service_rejection_for_closed_plan(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    closed_plan = _plan(
        status=ExecutionPlanStatus.OPERATOR_CONFIRMED
    )

    store.get_latest_execution_plan.return_value = closed_plan
    store.get_execution_plan.return_value = closed_plan

    store.get_latest_operator_confirmation_for_execution_plan.return_value = (
        _confirmation()
    )

    _patch_store(
        monkeypatch,
        store,
    )

    _feed_inputs(
        monkeypatch,
        [
            "EXECUTED",
            "10",
            "101.25",
            "",
            "",
            "",
        ],
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "confirm",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert "Operator confirmation unavailable." in out
    assert "not awaiting operator confirmation" in out

    store.save_operator_confirmation.assert_not_called()
    store.save_execution_plan.assert_not_called()
