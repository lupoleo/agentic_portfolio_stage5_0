from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.cio.execution_plan_service import ExecutionPlanService
from app.cio.models import CioDecisionType
from app.cio.storage import Stage3Store


OPPORTUNITY_ID = "OPP-SMCI-SERVICE"
PROPOSAL_ID = "PROP-SMCI-SERVICE"
SIMULATION_ID = "SIM-SMCI-SERVICE"
DECISION_ID = "DEC-SMCI-SERVICE"
INSTRUMENT_ID = "FIN-SMCI-SERVICE"


def _opportunity():
    return SimpleNamespace(
        opportunity_id=OPPORTUNITY_ID,
    )


def _decision(
    *,
    decision: CioDecisionType = CioDecisionType.ACCEPT,
    opportunity_id: str | None = OPPORTUNITY_ID,
    proposal_id: str = PROPOSAL_ID,
    simulation_id: str = SIMULATION_ID,
    hard_constraints_passed: bool | None = True,
    critical_evidence_complete: bool | None = True,
):
    return SimpleNamespace(
        decision_id=DECISION_ID,
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        simulation_id=simulation_id,
        decision=decision,
        hard_constraints_passed=hard_constraints_passed,
        critical_evidence_complete=critical_evidence_complete,
    )


def _proposal(
    *,
    opportunity_id: str = OPPORTUNITY_ID,
    instrument_id: str = INSTRUMENT_ID,
):
    return SimpleNamespace(
        proposal_id=PROPOSAL_ID,
        opportunity_id=opportunity_id,
        instrument_id=instrument_id,
    )


def _simulation():
    return SimpleNamespace(
        simulation_id=SIMULATION_ID,
    )


def _instrument():

    return SimpleNamespace(
        instrument_id=INSTRUMENT_ID,
        reference_underlying="SMCI",
        underlying="SMCI",
        description=(
            "Super Micro Computer Ordinary NASDAQ"
        ),
        fineco_symbol="SMCI",
        market="NASDAQ",
    )


def _plan(
    *,
    decision_id: str = DECISION_ID,
    broker_symbol: str | None = "SMCI",
):

    return SimpleNamespace(
        execution_plan_id="EXEC-SMCI-SERVICE",
        decision_id=decision_id,

        instrument_id=INSTRUMENT_ID,
        underlying="SMCI",

        instrument_description=(
            "Super Micro Computer Ordinary NASDAQ"
        ),

        broker_symbol=broker_symbol,

        market="NASDAQ",
    )


def _store_with_valid_chain() -> Mock:
    store = Mock(spec=Stage3Store)

    store.get_trade_opportunity.return_value = _opportunity()
    store.get_latest_cio_decision.return_value = _decision()
    store.get_trade_proposal.return_value = _proposal()
    store.get_portfolio_simulation.return_value = _simulation()
    store.get_fineco_instrument.return_value = _instrument()

    store.get_latest_execution_plan_for_proposal.return_value = None

    return store


def test_create_execution_plan_resolves_chain_builds_and_persists():

    store = _store_with_valid_chain()

    service = ExecutionPlanService(
        store
    )

    plan = _plan()

    service.builder = Mock()
    service.builder.build.return_value = plan

    result = service.create_execution_plan(
        OPPORTUNITY_ID,
        execution_notes="Manual review before Fineco execution.",
    )

    assert result is plan

    store.get_trade_opportunity.assert_called_once_with(
        OPPORTUNITY_ID
    )

    store.get_latest_cio_decision.assert_called_once_with(
        OPPORTUNITY_ID
    )

    store.get_trade_proposal.assert_called_once_with(
        PROPOSAL_ID
    )

    store.get_portfolio_simulation.assert_called_once_with(
        SIMULATION_ID
    )

    store.get_fineco_instrument.assert_called_once_with(
        INSTRUMENT_ID
    )

    store.get_latest_execution_plan_for_proposal.assert_called_once_with(
        PROPOSAL_ID
    )

    service.builder.build.assert_called_once_with(
        proposal=store.get_trade_proposal.return_value,
        simulation=store.get_portfolio_simulation.return_value,
        decision=store.get_latest_cio_decision.return_value,
        instrument=store.get_fineco_instrument.return_value,
        execution_notes="Manual review before Fineco execution.",
    )

    store.save_execution_plan.assert_called_once_with(
        plan
    )


def test_existing_execution_plan_for_same_decision_is_returned_idempotently():

    store = _store_with_valid_chain()

    existing = _plan()

    store.get_latest_execution_plan_for_proposal.return_value = (
        existing
    )

    service = ExecutionPlanService(
        store
    )

    service.builder = Mock()

    result = service.create_execution_plan(
        OPPORTUNITY_ID
    )

    assert result is existing

    service.builder.build.assert_not_called()
    store.save_execution_plan.assert_not_called()


def test_execution_plan_is_rebuilt_when_existing_plan_uses_older_decision():

    store = _store_with_valid_chain()

    existing = _plan(
        decision_id="DEC-OLDER"
    )

    store.get_latest_execution_plan_for_proposal.return_value = (
        existing
    )

    service = ExecutionPlanService(
        store
    )

    new_plan = _plan()

    service.builder = Mock()
    service.builder.build.return_value = (
        new_plan
    )

    result = service.create_execution_plan(
        OPPORTUNITY_ID
    )

    assert result is new_plan

    service.builder.build.assert_called_once()
    store.save_execution_plan.assert_called_once_with(
        new_plan
    )


def test_missing_opportunity_is_rejected():

    store = _store_with_valid_chain()

    store.get_trade_opportunity.return_value = None

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match="TradeOpportunity not found",
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )


def test_missing_cio_decision_is_rejected():

    store = _store_with_valid_chain()

    store.get_latest_cio_decision.return_value = None

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match="No CioDecision exists",
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )


def test_latest_decision_must_belong_to_opportunity():

    store = _store_with_valid_chain()

    store.get_latest_cio_decision.return_value = _decision(
        opportunity_id="OPP-OTHER"
    )

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )


@pytest.mark.parametrize(
    "decision_type",
    [
        CioDecisionType.MODIFY,
        CioDecisionType.REJECT,
    ],
)
def test_latest_non_accept_decision_is_rejected(
    decision_type,
):

    store = _store_with_valid_chain()

    store.get_latest_cio_decision.return_value = _decision(
        decision=decision_type
    )

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match="latest CIO decision to be ACCEPT",
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )


@pytest.mark.parametrize(
    (
        "hard_constraints_passed",
        "critical_evidence_complete",
        "message",
    ),
    [
        (
            False,
            True,
            "hard_constraints_passed=True",
        ),
        (
            True,
            False,
            "critical_evidence_complete=True",
        ),
        (
            None,
            True,
            "hard_constraints_passed=True",
        ),
        (
            True,
            None,
            "critical_evidence_complete=True",
        ),
    ],
)
def test_accept_requires_complete_governance_flags(
    hard_constraints_passed,
    critical_evidence_complete,
    message,
):

    store = _store_with_valid_chain()

    store.get_latest_cio_decision.return_value = _decision(
        hard_constraints_passed=hard_constraints_passed,
        critical_evidence_complete=critical_evidence_complete,
    )

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match=message,
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )


def test_missing_proposal_referenced_by_decision_is_rejected():

    store = _store_with_valid_chain()

    store.get_trade_proposal.return_value = None

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match="TradeProposal referenced by CioDecision was not found",
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )


def test_decision_proposal_must_belong_to_opportunity():

    store = _store_with_valid_chain()

    store.get_trade_proposal.return_value = _proposal(
        opportunity_id="OPP-OTHER"
    )

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match="TradeProposal does not belong",
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )


def test_missing_simulation_referenced_by_decision_is_rejected():

    store = _store_with_valid_chain()

    store.get_portfolio_simulation.return_value = None

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match="PortfolioSimulation referenced by CioDecision was not found",
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )


def test_missing_fineco_instrument_is_rejected():

    store = _store_with_valid_chain()

    store.get_fineco_instrument.return_value = None

    service = ExecutionPlanService(
        store
    )

    with pytest.raises(
        ValueError,
        match="Fineco instrument referenced by approved TradeProposal",
    ):
        service.create_execution_plan(
            OPPORTUNITY_ID
        )

def test_execution_plan_is_rebuilt_when_broker_metadata_changes():

    store = _store_with_valid_chain()

    # Existing plan was materialized before the Fineco symbol
    # was known.
    existing = _plan(
        broker_symbol=None,
    )

    store.get_latest_execution_plan_for_proposal.return_value = (
        existing
    )

    service = ExecutionPlanService(
        store
    )

    rebuilt = _plan(
        broker_symbol="SMCI",
    )

    service.builder = Mock()
    service.builder.build.return_value = rebuilt

    result = service.create_execution_plan(
        OPPORTUNITY_ID
    )

    assert result is rebuilt

    service.builder.build.assert_called_once()

    store.save_execution_plan.assert_called_once_with(
        rebuilt
    )