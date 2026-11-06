"""Canonical Stage 3 adapter for E2E-S2.2G."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.cio.cio_decision_service import CioDecisionService
from app.cio.execution_plan_service import ExecutionPlanService
from app.cio.instrument_selector import InstrumentSelector
from app.cio.models import CioDecisionType, TradeOpportunity
from app.cio.portfolio_lifecycle_gate import PortfolioLifecycleGate
from app.cio.position_sizer import PositionSizer
from app.cio.trade_proposal_service import TradeProposalService
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeStatus,
    ResearchHypothesisKind,
)
from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunStage,
    DryRunStageResult,
    DryRunStageStatus,
    DryRunTerminalReason,
    SelectedOpportunityDryRunRequest,
)


class DryRunStageExecutor(Protocol):
    def execute(
        self,
        stage: DryRunStage,
        request: SelectedOpportunityDryRunRequest,
        run_id: str,
    ) -> DryRunStageResult: ...


def validate_selected_opportunity(
    request: SelectedOpportunityDryRunRequest,
    *,
    integration_store,
    stage3_store,
) -> DryRunStageResult:
    """Validate the complete S2.2F provenance boundary before any mutation."""

    opportunity_id = request.opportunity_id
    if opportunity_id is None:
        return DryRunStageResult(
            stage=DryRunStage.SELECTION_VALIDATION,
            status=DryRunStageStatus.BLOCKED,
            reason=DryRunTerminalReason.NO_SELECTABLE_OPPORTUNITY,
            message="The Scanner-to-Research run exposes no selected opportunity",
        )

    integration_run = integration_store.get_run(request.scanner_research_run_id)
    if integration_run is None:
        return _invalid("ScannerResearchRun was not found")
    if opportunity_id not in integration_run.opportunity_ids:
        return _invalid("Selected opportunity is not materialized by the supplied S2.2F run")
    if integration_run.portfolio_snapshot_id != request.portfolio_snapshot_id:
        return _snapshot_mismatch("S2.2F run uses a different portfolio snapshot")

    outcomes = integration_store.list_outcomes(request.scanner_research_run_id)
    matches = [value for value in outcomes if value.opportunity_id == opportunity_id]
    if len(matches) != 1:
        return _invalid("Selected opportunity must have exactly one S2.2F outcome")
    outcome = matches[0]
    if outcome.status is not HypothesisOutcomeStatus.OPPORTUNITY_CREATED:
        return _invalid("Selected S2.2F outcome is not OPPORTUNITY_CREATED")
    if outcome.kind not in {
        ResearchHypothesisKind.NEW_LONG,
        ResearchHypothesisKind.NEW_SHORT,
    }:
        return _invalid("Portfolio-monitor hypotheses cannot enter S2.2G")

    link = integration_store.get_provenance_link(opportunity_id)
    if link is None:
        return _invalid("TradeOpportunity provenance link was not found")
    if link.integration_run_id != request.scanner_research_run_id:
        return _invalid("Provenance link belongs to another S2.2F run")
    if link.portfolio_snapshot_id != request.portfolio_snapshot_id:
        return _snapshot_mismatch("Provenance link uses a different portfolio snapshot")

    opportunity = stage3_store.get_trade_opportunity(opportunity_id)
    if opportunity is None:
        return _invalid("Persisted TradeOpportunity was not found")
    if opportunity.snapshot_id != request.portfolio_snapshot_id:
        return _snapshot_mismatch("TradeOpportunity uses a different portfolio snapshot")
    if opportunity.created_at > request.as_of:
        return _invalid("TradeOpportunity was created after dry-run as_of")

    if request.policy.require_current_snapshot:
        current = stage3_store.get_latest_portfolio_snapshot()
        if current is None or current.snapshot_id != request.portfolio_snapshot_id:
            return DryRunStageResult(
                stage=DryRunStage.SELECTION_VALIDATION,
                status=DryRunStageStatus.BLOCKED,
                reason=DryRunTerminalReason.STALE_PORTFOLIO_SNAPSHOT,
                message="Selected opportunity does not use the current portfolio snapshot",
            )

    return DryRunStageResult(
        stage=DryRunStage.SELECTION_VALIDATION,
        status=DryRunStageStatus.COMPLETED,
        output_ids=(opportunity_id, link.hypothesis_id, link.opportunity_score_id),
        output_payload={
            "direction": opportunity.direction.value,
            "ticker": opportunity.ticker,
            "hypothesis_id": link.hypothesis_id,
            "research_id": link.research_id,
            "opportunity_score_id": link.opportunity_score_id,
        },
        message="Exactly one provenance-complete S2.2F opportunity was selected",
    )


def _invalid(message: str) -> DryRunStageResult:
    return DryRunStageResult(
        stage=DryRunStage.SELECTION_VALIDATION,
        status=DryRunStageStatus.BLOCKED,
        reason=DryRunTerminalReason.INVALID_SELECTION,
        message=message,
    )


def _snapshot_mismatch(message: str) -> DryRunStageResult:
    return DryRunStageResult(
        stage=DryRunStage.SELECTION_VALIDATION,
        status=DryRunStageStatus.BLOCKED,
        reason=DryRunTerminalReason.PORTFOLIO_SNAPSHOT_MISMATCH,
        message=message,
    )


class CanonicalSelectedOpportunityExecutor:
    """Connects S2.2G to frozen Stage 3 services without policy duplication."""

    def __init__(
        self,
        *,
        stage3_store,
        portfolio_filter_service,
        portfolio_simulation_service,
        integration_store,
    ) -> None:
        self.store = stage3_store
        self.integration_store = integration_store
        self.portfolio_filter = portfolio_filter_service
        self.lifecycle_gate = PortfolioLifecycleGate(stage3_store)
        self.selector = InstrumentSelector()
        self.sizer = PositionSizer()
        self.proposal_service = TradeProposalService(stage3_store)
        self.simulation_service = portfolio_simulation_service
        self.decision_service = CioDecisionService(stage3_store)
        self.execution_plan_service = ExecutionPlanService(stage3_store)

    def execute(
        self,
        stage: DryRunStage,
        request: SelectedOpportunityDryRunRequest,
        run_id: str,
    ) -> DryRunStageResult:
        if stage is DryRunStage.SELECTION_VALIDATION:
            return validate_selected_opportunity(
                request,
                integration_store=self.integration_store,
                stage3_store=self.store,
            )
        method = getattr(self, f"_execute_{stage.value.lower()}")
        return method(request, run_id)

    def _opportunity(self, request) -> TradeOpportunity:
        value = self.store.get_trade_opportunity(request.opportunity_id)
        if value is None:
            raise ValueError(f"TradeOpportunity not found: {request.opportunity_id}")
        return value

    def _execute_opportunity_preparation(self, request, _run_id):
        parameters = request.parameters
        if parameters is None:
            return self._blocked(
                DryRunStage.OPPORTUNITY_PREPARATION,
                DryRunTerminalReason.MARKET_INPUT_UNAVAILABLE,
                "Explicit exposure, risk and market inputs are required",
            )
        opportunity = self._opportunity(request)
        conflicts = []
        if opportunity.target_exposure_eur not in {None, parameters.requested_exposure_eur}:
            conflicts.append("target_exposure_eur")
        if opportunity.max_intended_loss_eur not in {None, parameters.max_intended_loss_eur}:
            conflicts.append("max_intended_loss_eur")
        if conflicts:
            return self._blocked(
                DryRunStage.OPPORTUNITY_PREPARATION,
                DryRunTerminalReason.OPERATOR_INPUT_CONFLICT,
                "Operator overlay conflicts with persisted opportunity: " + ", ".join(conflicts),
            )
        data = opportunity.model_dump()
        data.update({
            "target_exposure_eur": parameters.requested_exposure_eur,
            "max_intended_loss_eur": parameters.max_intended_loss_eur,
            "updated_at": request.as_of,
            "notes": self._append_note(
                opportunity.notes,
                f"S2.2G operator overlay; source={parameters.input_source}",
            ),
        })
        prepared = TradeOpportunity.model_validate(data)
        self.store.save_trade_opportunity(prepared)
        return self._completed(
            DryRunStage.OPPORTUNITY_PREPARATION,
            (prepared.opportunity_id,),
            "Explicit operator inputs were applied and persisted",
            {
                "target_exposure_eur": prepared.target_exposure_eur,
                "max_intended_loss_eur": prepared.max_intended_loss_eur,
                "market_observed_at": parameters.market_observed_at.isoformat(),
            },
        )

    def _execute_portfolio_filter(self, request, run_id):
        assessment = self.portfolio_filter.assess_and_persist(
            request.opportunity_id,
            as_of=request.as_of,
            assessment_id=f"S22G-PF-{run_id}",
        )
        return self._completed(
            DryRunStage.PORTFOLIO_FILTER,
            (assessment.assessment_id,),
            f"Portfolio Filter returned {assessment.decision.value}",
            {"decision": assessment.decision.value},
        )

    def _execute_portfolio_lifecycle_gate(self, request, _run_id):
        assessment = self.store.get_latest_portfolio_fit_assessment(request.opportunity_id)
        if assessment is None:
            raise ValueError("No PortfolioFitAssessment exists for selected opportunity")
        result = self.lifecycle_gate.evaluate(assessment.assessment_id)
        if not result.can_advance:
            return self._blocked(
                DryRunStage.PORTFOLIO_LIFECYCLE_GATE,
                DryRunTerminalReason.PORTFOLIO_FILTER_BLOCKED,
                result.reason,
                (assessment.assessment_id,),
                {"gate_reason": result.reason_code.value},
            )
        return self._completed(
            DryRunStage.PORTFOLIO_LIFECYCLE_GATE,
            (assessment.assessment_id,),
            result.reason,
            {"gate_reason": result.reason_code.value},
        )

    def _execute_instrument_selection(self, request, _run_id):
        opportunity = self._opportunity(request)
        instruments = self.store.find_fineco_instruments(opportunity.ticker)
        candidates = self.selector.select(opportunity, instruments)
        self.store.replace_instrument_candidates(opportunity.opportunity_id, candidates)
        eligible = [value for value in candidates if value.eligible]
        if request.parameters and request.parameters.instrument_id:
            eligible = [
                value for value in eligible
                if value.instrument_id == request.parameters.instrument_id
            ]
        if not eligible:
            return self._blocked(
                DryRunStage.INSTRUMENT_SELECTION,
                DryRunTerminalReason.NO_ELIGIBLE_INSTRUMENT,
                "No eligible cached broker instrument implements the selected opportunity",
                diagnostics=tuple(value.rejection_reason or "INELIGIBLE" for value in candidates),
            )
        self.store.mark_trade_opportunity_instruments_ranked(opportunity.opportunity_id)
        selected = eligible[0]
        return self._completed(
            DryRunStage.INSTRUMENT_SELECTION,
            (selected.instrument_id,),
            "Deterministic Instrument Selector produced an eligible instrument",
            {"instrument_id": selected.instrument_id, "candidate_count": len(candidates)},
        )

    def _execute_position_sizing(self, request, _run_id):
        parameters = request.parameters
        if parameters is None:
            return self._blocked(
                DryRunStage.POSITION_SIZING,
                DryRunTerminalReason.MARKET_INPUT_UNAVAILABLE,
                "Explicit sizing market inputs are unavailable",
            )
        opportunity = self._opportunity(request)
        candidate = self.store.get_top_instrument_candidate(opportunity.opportunity_id)
        if parameters.instrument_id:
            candidate = next(
                (
                    value for value in self.store.list_instrument_candidates(opportunity.opportunity_id)
                    if value.eligible and value.instrument_id == parameters.instrument_id
                ),
                None,
            )
        if candidate is None:
            return self._blocked(
                DryRunStage.POSITION_SIZING,
                DryRunTerminalReason.NO_ELIGIBLE_INSTRUMENT,
                "No selected instrument candidate is available for sizing",
            )
        instrument = self.store.get_fineco_instrument(candidate.instrument_id)
        account = self.store.get_latest_account_state()
        snapshot = self.store.get_portfolio_snapshot(request.portfolio_snapshot_id)
        if account is None and snapshot is not None and snapshot.account_state_id:
            account = self.store.get_account_state(snapshot.account_state_id)
        if instrument is None or account is None:
            raise ValueError("Instrument or AccountState missing before Position Sizing")
        sizing = self.sizer.size(
            opportunity=opportunity,
            instrument=instrument,
            account_state=account,
            reference_price=parameters.reference_price,
            fx_to_eur=parameters.fx_to_eur,
            stop_price=parameters.stop_price,
            requested_exposure_eur=parameters.requested_exposure_eur,
        )
        self.store.save_position_sizing(sizing)
        if not sizing.constraints_passed:
            return self._blocked(
                DryRunStage.POSITION_SIZING,
                DryRunTerminalReason.POSITION_SIZING_BLOCKED,
                "Position Sizing failed one or more constraints",
                (sizing.sizing_id,),
                diagnostics=tuple(sizing.violated_constraints),
            )
        self.store.mark_trade_opportunity_position_sized(
            opportunity.opportunity_id, sizing.sizing_id
        )
        return self._completed(
            DryRunStage.POSITION_SIZING,
            (sizing.sizing_id,),
            "Position Sizing produced a constraint-compliant quantity",
            {"quantity": sizing.quantity, "instrument_id": sizing.instrument_id},
        )

    def _execute_trade_proposal(self, request, _run_id):
        p = request.parameters
        proposal = self.proposal_service.create_preliminary_proposal(
            request.opportunity_id,
            entry_type=p.entry_type,
            entry_price=p.entry_price,
            target_1=p.target_1,
            target_2=p.target_2,
        )
        return self._completed(
            DryRunStage.TRADE_PROPOSAL,
            (proposal.proposal_id,),
            "Canonical TradeProposal was persisted",
            {"proposal_id": proposal.proposal_id},
        )

    def _execute_portfolio_simulation(self, request, _run_id):
        simulation = self.simulation_service.create_simulation(request.opportunity_id)
        return self._completed(
            DryRunStage.PORTFOLIO_SIMULATION,
            (simulation.simulation_id,),
            "Portfolio Simulator V2 persisted the hypothetical before/after state",
            {"constraints_passed": simulation.constraints_passed},
        )

    def _execute_cio_decision(self, request, _run_id):
        decision = self.decision_service.create_decision(request.opportunity_id)
        if decision.decision is CioDecisionType.REJECT:
            return self._blocked(
                DryRunStage.CIO_DECISION,
                DryRunTerminalReason.CIO_REJECTED,
                decision.rationale,
                (decision.decision_id,),
                {"decision": decision.decision.value},
            )
        if decision.decision is CioDecisionType.MODIFY:
            return self._blocked(
                DryRunStage.CIO_DECISION,
                DryRunTerminalReason.CIO_MODIFY_REQUIRED,
                decision.rationale,
                (decision.decision_id,),
                {"decision": decision.decision.value},
            )
        return self._completed(
            DryRunStage.CIO_DECISION,
            (decision.decision_id,),
            "CIO accepted the simulated proposal",
            {"decision": decision.decision.value},
        )

    def _execute_execution_plan(self, request, run_id):
        plan = self.execution_plan_service.create_execution_plan(
            request.opportunity_id,
            execution_notes=(
                f"DRY RUN ONLY — NOT AUTHORIZED FOR BROKER EXECUTION; S2.2G run={run_id}"
            ),
        )
        return self._completed(
            DryRunStage.EXECUTION_PLAN,
            (plan.execution_plan_id,),
            "Manual ExecutionPlan created in non-executing dry-run envelope",
            {
                "execution_plan_id": plan.execution_plan_id,
                "execution_plan_status": plan.status.value,
                "execution_authorized": False,
            },
        )

    @staticmethod
    def _append_note(existing: str | None, addition: str) -> str:
        return f"{existing}; {addition}" if existing else addition

    @staticmethod
    def _completed(stage, output_ids, message, payload):
        return DryRunStageResult(
            stage=stage,
            status=DryRunStageStatus.COMPLETED,
            output_ids=tuple(output_ids),
            output_payload=payload,
            message=message,
        )

    @staticmethod
    def _blocked(
        stage,
        reason,
        message,
        output_ids=(),
        payload=None,
        diagnostics=(),
    ):
        return DryRunStageResult(
            stage=stage,
            status=DryRunStageStatus.BLOCKED,
            output_ids=tuple(output_ids),
            output_payload=payload or {},
            reason=reason,
            message=message,
            diagnostics=tuple(diagnostics),
        )
