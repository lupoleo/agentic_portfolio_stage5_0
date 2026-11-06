from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.cio.models import (
    Currency,
    Direction,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionSide,
)
from app.cio.storage import Stage3Store


NOW = datetime(
    2026,
    8,
    25,
    18,
    30,
    tzinfo=timezone.utc,
)


def _plan(
    *,
    execution_plan_id: str = "EXEC-SMCI-001",
    created_at: datetime = NOW,
    opportunity_id: str = "OPP-SMCI-001",
    proposal_id: str = "PROP-SMCI-001",
    simulation_id: str = "SIM-SMCI-001",
    decision_id: str = "DEC-SMCI-001",
    snapshot_id: str = "SNAP-SMCI-001",
    instrument_id: str = "FIN-SMCI-001",
    status: ExecutionPlanStatus = (
        ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
    ),
) -> ExecutionPlan:

    return ExecutionPlan(
        execution_plan_id=execution_plan_id,
        created_at=created_at,
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        simulation_id=simulation_id,
        decision_id=decision_id,
        snapshot_id=snapshot_id,
        broker="FINECO",
        underlying="SMCI",
        instrument_id=instrument_id,
        instrument_description="Super Micro Computer Ordinary NASDAQ",
        broker_symbol="SMCI",
        market="NASDAQ",
        currency=Currency.USD,
        direction=Direction.SHORT,
        execution_side=ExecutionSide.SELL_SHORT,
        quantity=429,
        order_type="MARKET",
        reference_price=37.24,
        entry_price=None,
        stop_price=39.0,
        target_1=35.0,
        target_2=33.0,
        fx_to_eur=0.86,
        gross_exposure_eur=13739.33,
        estimated_capital_required_eur=13739.33,
        estimated_max_loss_eur=649.33,
        expected_holding_min_days=4,
        expected_holding_max_days=10,
        status=status,
        execution_notes="Manual Fineco execution only.",
    )


def _install_valid_chain(
    monkeypatch,
    store: Stage3Store,
    plan: ExecutionPlan,
) -> None:
    """
    Stub only the already-tested upstream retrieval layer.

    These tests are intentionally focused on ExecutionPlan storage:
    schema, JSON round-trip, ordering and provenance validation.
    """

    monkeypatch.setattr(
        store,
        "get_trade_opportunity",
        lambda _id: SimpleNamespace(
            opportunity_id=plan.opportunity_id,
            snapshot_id=plan.snapshot_id,
        ),
    )

    monkeypatch.setattr(
        store,
        "get_trade_proposal",
        lambda _id: SimpleNamespace(
            proposal_id=plan.proposal_id,
            opportunity_id=plan.opportunity_id,
            snapshot_id=plan.snapshot_id,
            instrument_id=plan.instrument_id,
        ),
    )

    monkeypatch.setattr(
        store,
        "get_portfolio_simulation",
        lambda _id: SimpleNamespace(
            simulation_id=plan.simulation_id,
            proposal_id=plan.proposal_id,
            snapshot_id=plan.snapshot_id,
        ),
    )

    monkeypatch.setattr(
        store,
        "get_cio_decision",
        lambda _id: SimpleNamespace(
            decision_id=plan.decision_id,
            opportunity_id=plan.opportunity_id,
            proposal_id=plan.proposal_id,
            simulation_id=plan.simulation_id,
            snapshot_id=plan.snapshot_id,
        ),
    )

    monkeypatch.setattr(
        store,
        "get_portfolio_snapshot",
        lambda _id: SimpleNamespace(
            snapshot_id=plan.snapshot_id,
        ),
    )

    monkeypatch.setattr(
        store,
        "get_fineco_instrument",
        lambda _id: SimpleNamespace(
            instrument_id=plan.instrument_id,
        ),
    )


def test_execution_plan_save_and_get_round_trip(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    store.save_execution_plan(
        plan
    )

    loaded = store.get_execution_plan(
        plan.execution_plan_id
    )

    assert loaded is not None
    assert loaded == plan
    assert (
        loaded.status
        == ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
    )
    assert loaded.instrument_id == "FIN-SMCI-001"
    assert loaded.quantity == 429


def test_execution_plan_upsert_updates_payload(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    original = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        original,
    )

    store.save_execution_plan(
        original
    )

    data = original.model_dump()
    data["execution_notes"] = "Updated operator note."

    updated = ExecutionPlan.model_validate(
        data
    )

    store.save_execution_plan(
        updated
    )

    loaded = store.get_execution_plan(
        original.execution_plan_id
    )

    assert loaded is not None
    assert (
        loaded.execution_notes
        == "Updated operator note."
    )


def test_execution_plan_list_is_newest_first(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    older = _plan(
        execution_plan_id="EXEC-SMCI-OLD",
        created_at=NOW,
    )

    newer = _plan(
        execution_plan_id="EXEC-SMCI-NEW",
        created_at=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    _install_valid_chain(
        monkeypatch,
        store,
        older,
    )

    store.save_execution_plan(
        older
    )

    _install_valid_chain(
        monkeypatch,
        store,
        newer,
    )

    store.save_execution_plan(
        newer
    )

    plans = store.list_execution_plans(
        older.opportunity_id
    )

    assert [
        plan.execution_plan_id
        for plan in plans
    ] == [
        "EXEC-SMCI-NEW",
        "EXEC-SMCI-OLD",
    ]


def test_latest_execution_plan_returns_newest(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    older = _plan(
        execution_plan_id="EXEC-SMCI-OLD",
        created_at=NOW,
    )

    newer = _plan(
        execution_plan_id="EXEC-SMCI-NEW",
        created_at=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    _install_valid_chain(
        monkeypatch,
        store,
        older,
    )
    store.save_execution_plan(
        older
    )

    _install_valid_chain(
        monkeypatch,
        store,
        newer,
    )
    store.save_execution_plan(
        newer
    )

    latest = store.get_latest_execution_plan(
        older.opportunity_id
    )

    assert latest is not None
    assert (
        latest.execution_plan_id
        == "EXEC-SMCI-NEW"
    )


def test_latest_execution_plan_for_proposal(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    store.save_execution_plan(
        plan
    )

    loaded = (
        store
        .get_latest_execution_plan_for_proposal(
            plan.proposal_id
        )
    )

    assert loaded is not None
    assert (
        loaded.execution_plan_id
        == plan.execution_plan_id
    )


def test_execution_plan_missing_opportunity_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_trade_opportunity",
        lambda _id: None,
    )

    with pytest.raises(
        ValueError,
        match="TradeOpportunity not found",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_missing_proposal_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_trade_proposal",
        lambda _id: None,
    )

    with pytest.raises(
        ValueError,
        match="TradeProposal not found",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_missing_simulation_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_portfolio_simulation",
        lambda _id: None,
    )

    with pytest.raises(
        ValueError,
        match="PortfolioSimulation not found",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_missing_decision_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_cio_decision",
        lambda _id: None,
    )

    with pytest.raises(
        ValueError,
        match="CioDecision not found",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_missing_instrument_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_fineco_instrument",
        lambda _id: None,
    )

    with pytest.raises(
        ValueError,
        match="FinecoInstrument not found",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_proposal_opportunity_mismatch_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_trade_proposal",
        lambda _id: SimpleNamespace(
            proposal_id=plan.proposal_id,
            opportunity_id="OPP-OTHER",
            snapshot_id=plan.snapshot_id,
            instrument_id=plan.instrument_id,
        ),
    )

    with pytest.raises(
        ValueError,
        match="opportunity_id does not match",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_proposal_instrument_mismatch_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_trade_proposal",
        lambda _id: SimpleNamespace(
            proposal_id=plan.proposal_id,
            opportunity_id=plan.opportunity_id,
            snapshot_id=plan.snapshot_id,
            instrument_id="FIN-OTHER",
        ),
    )

    with pytest.raises(
        ValueError,
        match="instrument_id does not match",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_simulation_proposal_mismatch_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_portfolio_simulation",
        lambda _id: SimpleNamespace(
            simulation_id=plan.simulation_id,
            proposal_id="PROP-OTHER",
            snapshot_id=plan.snapshot_id,
        ),
    )

    with pytest.raises(
        ValueError,
        match="simulation_id does not belong",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_decision_simulation_mismatch_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_cio_decision",
        lambda _id: SimpleNamespace(
            decision_id=plan.decision_id,
            opportunity_id=plan.opportunity_id,
            proposal_id=plan.proposal_id,
            simulation_id="SIM-OTHER",
            snapshot_id=plan.snapshot_id,
        ),
    )

    with pytest.raises(
        ValueError,
        match="decision_id does not belong.*simulation_id",
    ):
        store.save_execution_plan(
            plan
        )


def test_execution_plan_snapshot_mismatch_rejected(
    tmp_path,
    monkeypatch,
):
    store = Stage3Store(
        tmp_path / "cio.db"
    )

    plan = _plan()

    _install_valid_chain(
        monkeypatch,
        store,
        plan,
    )

    monkeypatch.setattr(
        store,
        "get_trade_proposal",
        lambda _id: SimpleNamespace(
            proposal_id=plan.proposal_id,
            opportunity_id=plan.opportunity_id,
            snapshot_id="SNAP-OTHER",
            instrument_id=plan.instrument_id,
        ),
    )

    with pytest.raises(
        ValueError,
        match="snapshot_id does not match",
    ):
        store.save_execution_plan(
            plan
        )