from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import app.cio.cli as cli_module
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
    22,
    0,
    tzinfo=timezone.utc,
)

OPPORTUNITY_ID = "OPP-CLI-OUTCOME"


def _open_outcome() -> TradeOutcome:

    return TradeOutcome(
        outcome_id="OUT-CLI-001",
        created_at=NOW,
        updated_at=NOW,
        opportunity_id=OPPORTUNITY_ID,
        proposal_id="PROP-CLI-001",
        simulation_id="SIM-CLI-001",
        decision_id="DEC-CLI-001",
        execution_plan_id="EXEC-CLI-001",
        confirmation_id="CONF-CLI-001",
        snapshot_id="SNAP-CLI-001",
        ticker="TEST",
        instrument_id="FIN-CLI-001",
        direction=Direction.LONG,
        execution_side=ExecutionSide.BUY,
        currency=Currency.USD,
        entry_datetime=NOW,
        entry_quantity=10,
        entry_price=100.0,
        entry_commission_eur=5.0,
        entry_fx_to_eur=0.86,
        planned_reference_price=99.0,
        planned_stop_price=95.0,
        planned_target_1=110.0,
        planned_target_2=115.0,
        planned_max_loss_eur=45.0,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        status=TradeOutcomeStatus.OPEN,
        notes="CLI test.",
    )


def _closed_outcome() -> TradeOutcome:

    data = _open_outcome().model_dump()

    data.update(
        {
            "updated_at": NOW,
            "exit_datetime": NOW,
            "exit_quantity": 10,
            "exit_price": 110.0,
            "exit_commission_eur": 5.0,
            "exit_fx_to_eur": 0.87,
            "exit_reason": TradeExitReason.TARGET_1,
            "realized_pnl_eur": 87.0,
            "realized_return_pct": 10.116279,
            "holding_days": 0.0,
            "entry_slippage_pct": 1.010101,
            "status": TradeOutcomeStatus.CLOSED,
        }
    )

    return TradeOutcome.model_validate(
        data
    )


def _patch_store(
    monkeypatch,
    store,
):

    monkeypatch.setattr(
        cli_module,
        "Stage3Store",
        lambda _db: store,
    )


def _feed_inputs(
    monkeypatch,
    values,
):

    iterator = iter(values)

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt="": next(iterator),
    )


def test_cli_outcome_create_uses_service(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    _patch_store(
        monkeypatch,
        store,
    )

    outcome = _open_outcome()

    service = Mock()
    service.create_open_outcome.return_value = outcome

    monkeypatch.setattr(
        cli_module,
        "TradeOutcomeService",
        lambda _store: service,
    )

    _feed_inputs(
        monkeypatch,
        [""],
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "outcome",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert "TradeOutcome created/resolved." in out
    assert "Status:           OPEN" in out

    service.create_open_outcome.assert_called_once_with(
        OPPORTUNITY_ID,
        notes=None,
    )


def test_cli_outcome_show_is_read_only(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    store.get_latest_trade_outcome.return_value = (
        _open_outcome()
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
            "outcome-show",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert "CIO TRADE OUTCOME" in out
    assert "Status:           OPEN" in out

    store.save_trade_outcome.assert_not_called()


def test_cli_outcome_close_uses_service_and_prints_closed(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    store.get_latest_trade_outcome.return_value = (
        _open_outcome()
    )

    _patch_store(
        monkeypatch,
        store,
    )

    service = Mock()
    service.close_outcome.return_value = (
        _closed_outcome()
    )

    monkeypatch.setattr(
        cli_module,
        "TradeOutcomeService",
        lambda _store: service,
    )

    _feed_inputs(
        monkeypatch,
        [
            "2026-08-27T22:00:00+00:00",
            "110",
            "",
            "5",
            "0.87",
            "TARGET_1",
            "Closed in CLI test.",
        ],
    )

    cli_module.main(
        [
            "--db",
            str(tmp_path / "cio.db"),
            "opportunities",
            "outcome-close",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert "TradeOutcome closed and persisted." in out
    assert "Status:           CLOSED" in out
    assert "Realized P/L:     €87.00" in out

    service.close_outcome.assert_called_once()

    kwargs = service.close_outcome.call_args.kwargs

    assert kwargs["exit_price"] == 110.0
    assert kwargs["exit_quantity"] == 10
    assert kwargs["exit_commission_eur"] == 5.0
    assert kwargs["exit_fx_to_eur"] == 0.87
    assert kwargs["exit_reason"] == TradeExitReason.TARGET_1


def test_cli_outcome_close_rejects_already_closed_before_prompting(
    tmp_path,
    monkeypatch,
    capsys,
):

    store = Mock(
        spec=Stage3Store
    )

    store.get_latest_trade_outcome.return_value = (
        _closed_outcome()
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
            "outcome-close",
            OPPORTUNITY_ID,
        ]
    )

    out = capsys.readouterr().out

    assert "TradeOutcome close unavailable." in out
    assert "expected OPEN" in out
