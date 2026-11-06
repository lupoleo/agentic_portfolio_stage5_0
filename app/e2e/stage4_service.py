"""Resumable E2E-S4.0A orchestration over frozen business services."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.e2e.stage4_contracts import (
    IMMUTABLE_RUN_STATUSES,
    Stage4E2ERequest,
    Stage4E2ERun,
    Stage4Mode,
    Stage4RunStatus,
    Stage4Stage,
    Stage4StageRecord,
    Stage4StageResult,
    Stage4StageStatus,
    Stage4TerminalReason,
    canonical_fingerprint,
    stage4_request_fingerprint,
    stage4_run_id,
)


STAGE_ORDER = tuple(Stage4Stage)
EXTERNAL_STAGES = frozenset(
    {
        Stage4Stage.PORTFOLIO_ANALYSIS,
        Stage4Stage.SCANNER_DISCOVERY,
        Stage4Stage.WATCH_UNIVERSE_ASSEMBLY,
        Stage4Stage.RESEARCH_INTEGRATION,
        Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN,
    }
)


class Stage4LineageError(ValueError):
    pass


class Stage4SafetyError(ValueError):
    pass


class Stage4E2EService:
    def __init__(self, *, store, adapters) -> None:
        self.store = store
        self.adapters = adapters

    def run(
        self,
        request: Stage4E2ERequest,
        *,
        now: datetime | None = None,
    ) -> Stage4E2ERun:
        now = now or datetime.now(timezone.utc)
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if now < request.as_of:
            raise ValueError("E2E execution cannot precede request as_of")

        run_id = stage4_run_id(request)
        request_fp = stage4_request_fingerprint(request)
        existing = self.store.get_run(run_id)
        if existing is not None and existing.request_fingerprint != request_fp:
            raise ValueError("Stage 4 run identity collision")
        if existing is not None and existing.status in IMMUTABLE_RUN_STATUSES:
            return existing

        current = self._build_run(
            request,
            prior=existing,
            status=Stage4RunStatus.RUNNING,
            started_at=existing.started_at if existing else now,
        )
        self.store.save_run(current, request)

        for stage in STAGE_ORDER:
            persisted = self.store.get_stage(run_id, stage)
            if persisted is not None:
                if persisted.status is not Stage4StageStatus.COMPLETED:
                    raise ValueError("non-completed persisted stage cannot resume")
                current = self._apply_output(current, stage, persisted.output_payload)
                continue

            try:
                result = self._execute(stage, request, current)
                self._validate_result(request, current, result)
            except Stage4LineageError as exc:
                result = Stage4StageResult(
                    stage=stage,
                    status=Stage4StageStatus.BLOCKED,
                    reason=Stage4TerminalReason.LINEAGE_MISMATCH,
                    message=str(exc),
                    diagnostics=(repr(exc),),
                )
            except Stage4SafetyError as exc:
                result = Stage4StageResult(
                    stage=stage,
                    status=Stage4StageStatus.BLOCKED,
                    reason=Stage4TerminalReason.SAFETY_INVARIANT_VIOLATION,
                    message=str(exc),
                    diagnostics=(repr(exc),),
                )
            except Exception as exc:
                result = Stage4StageResult(
                    stage=stage,
                    status=Stage4StageStatus.FAILED,
                    reason=Stage4TerminalReason.STAGE_FAILED,
                    message=f"{type(exc).__name__}: {exc}",
                    diagnostics=(repr(exc),),
                )

            record = Stage4StageRecord(
                run_id=run_id,
                stage=stage,
                status=result.status,
                started_at=now,
                completed_at=now,
                input_ids=self._input_ids(current),
                output_ids=result.output_ids,
                input_fingerprint=current.fingerprint,
                output_fingerprint=canonical_fingerprint(
                    result.output_payload
                ),
                output_payload=result.output_payload,
                reason=result.reason,
                message=result.message,
                diagnostics=result.diagnostics,
                network_calls=result.network_calls,
            )
            if result.status is Stage4StageStatus.PARTIAL:
                self.store.save_attempt(record)
            else:
                self.store.save_stage(record)

            if result.status is not Stage4StageStatus.COMPLETED:
                evidence_prior = current
                if result.status is Stage4StageStatus.PARTIAL:
                    evidence_prior = self._apply_output(
                        current,
                        stage,
                        result.output_payload,
                        mark_completed=False,
                    )
                final_status = {
                    Stage4StageStatus.WAITING_FOR_OPERATOR_SELECTION: (
                        Stage4RunStatus.WAITING_FOR_OPERATOR_SELECTION
                    ),
                    Stage4StageStatus.BLOCKED: Stage4RunStatus.BLOCKED,
                    Stage4StageStatus.PARTIAL: Stage4RunStatus.PARTIAL,
                    Stage4StageStatus.FAILED: Stage4RunStatus.FAILED,
                }[result.status]
                final = self._build_run(
                    request,
                    prior=evidence_prior,
                    status=final_status,
                    terminal_reason=result.reason,
                    completed_at=now,
                    network_calls=current.network_calls + result.network_calls,
                )
                self.store.save_run(final, request)
                return final

            current = self._apply_output(current, stage, result.output_payload)
            current = self._build_run(
                request,
                prior=current,
                status=(
                    Stage4RunStatus.COMPLETED
                    if stage is Stage4Stage.FINAL_EVIDENCE
                    else Stage4RunStatus.RUNNING
                ),
                terminal_reason=(
                    Stage4TerminalReason.E2E_DRY_RUN_COMPLETED
                    if stage is Stage4Stage.FINAL_EVIDENCE
                    else None
                ),
                completed_at=now if stage is Stage4Stage.FINAL_EVIDENCE else None,
                network_calls=current.network_calls + result.network_calls,
            )
            self.store.save_run(current, request)

        return current

    def _execute(self, stage, request, current) -> Stage4StageResult:
        if stage in EXTERNAL_STAGES:
            return self.adapters.execute(stage, request, current)
        if stage is Stage4Stage.OPERATOR_SELECTION:
            selected = request.selected_opportunity_id
            if selected is None:
                return Stage4StageResult(
                    stage=stage,
                    status=Stage4StageStatus.WAITING_FOR_OPERATOR_SELECTION,
                    reason=Stage4TerminalReason.WAITING_FOR_OPERATOR_SELECTION,
                    message="one explicit TradeOpportunity selection is required",
                )
            if selected not in current.selectable_opportunity_ids:
                return Stage4StageResult(
                    stage=stage,
                    status=Stage4StageStatus.BLOCKED,
                    reason=Stage4TerminalReason.LINEAGE_MISMATCH,
                    message="selected opportunity does not belong to the research run",
                )
            return Stage4StageResult(
                stage=stage,
                status=Stage4StageStatus.COMPLETED,
                output_ids=(selected,),
                output_payload={"selected_opportunity_id": selected},
                message="explicit operator selection accepted",
            )
        if stage is Stage4Stage.FINAL_EVIDENCE:
            if not current.execution_plan_id:
                raise ValueError("final evidence requires an ExecutionPlan")
            return Stage4StageResult(
                stage=stage,
                status=Stage4StageStatus.COMPLETED,
                output_ids=(current.execution_plan_id,),
                output_payload={
                    "execution_plan_id": current.execution_plan_id,
                    "dry_run": True,
                    "execution_authorized": False,
                    "broker_orders_submitted": 0,
                    "portfolio_mutations": 0,
                    "automatic_executions": 0,
                },
                message="complete E2E dry-run evidence validated",
            )
        raise ValueError(f"unsupported Stage 4 stage: {stage.value}")

    @staticmethod
    def _validate_result(request, current, result) -> None:
        if request.mode is Stage4Mode.CACHE_ONLY and result.network_calls:
            raise Stage4SafetyError("cache-only Stage 4 execution made network calls")
        payload = result.output_payload
        stage = result.stage
        required = {
            Stage4Stage.PORTFOLIO_ANALYSIS: (
                "portfolio_snapshot_id", "portfolio_risk_state_id",
            ),
            Stage4Stage.SCANNER_DISCOVERY: ("scanner_run_id",),
            Stage4Stage.WATCH_UNIVERSE_ASSEMBLY: (
                "watch_universe_run_id", "watch_universe_fingerprint",
                "watch_universe_report_path", "portfolio_snapshot_id",
            ),
            Stage4Stage.RESEARCH_INTEGRATION: (
                "research_run_id", "watch_universe_run_id",
                "watch_universe_fingerprint", "portfolio_snapshot_id",
                "opportunity_ids",
            ),
            Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN: (
                "selected_opportunity_dry_run_id", "research_run_id",
                "opportunity_id", "portfolio_snapshot_id", "execution_plan_id",
                "dry_run", "execution_authorized", "broker_orders_submitted",
                "portfolio_mutations", "automatic_executions",
            ),
            Stage4Stage.FINAL_EVIDENCE: (
                "execution_plan_id", "dry_run", "execution_authorized",
                "broker_orders_submitted", "portfolio_mutations",
                "automatic_executions",
            ),
        }.get(stage, ())
        if result.status is Stage4StageStatus.COMPLETED:
            missing = [key for key in required if key not in payload]
            if missing:
                raise ValueError(f"{stage.value} output missing: {missing}")

        if result.status is not Stage4StageStatus.COMPLETED:
            return
        if stage in {
            Stage4Stage.WATCH_UNIVERSE_ASSEMBLY,
            Stage4Stage.RESEARCH_INTEGRATION,
            Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN,
        } and payload.get("portfolio_snapshot_id") != current.portfolio_snapshot_id:
            raise Stage4LineageError("portfolio snapshot lineage mismatch")
        if stage is Stage4Stage.RESEARCH_INTEGRATION:
            if payload.get("watch_universe_run_id") != current.watch_universe_run_id:
                raise Stage4LineageError("watch-universe run lineage mismatch")
            if payload.get("watch_universe_fingerprint") != current.watch_universe_fingerprint:
                raise Stage4LineageError("watch-universe fingerprint mismatch")
        if stage is Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN:
            if payload.get("research_run_id") != current.research_run_id:
                raise Stage4LineageError("research run lineage mismatch")
            if payload.get("opportunity_id") != current.selected_opportunity_id:
                raise Stage4LineageError("selected opportunity lineage mismatch")
            if (
                payload.get("dry_run") is not True
                or payload.get("execution_authorized") is not False
                or any(payload.get(key) != 0 for key in (
                    "broker_orders_submitted", "portfolio_mutations",
                    "automatic_executions",
                ))
            ):
                raise Stage4SafetyError("downstream dry-run safety invariant violated")

    @staticmethod
    def _apply_output(
        run,
        stage,
        payload,
        *,
        mark_completed=True,
    ):
        updates: dict[str, Any] = {}
        keys = {
            Stage4Stage.PORTFOLIO_ANALYSIS: (
                "portfolio_snapshot_id", "portfolio_risk_state_id",
            ),
            Stage4Stage.SCANNER_DISCOVERY: ("scanner_run_id",),
            Stage4Stage.WATCH_UNIVERSE_ASSEMBLY: (
                "watch_universe_run_id", "watch_universe_fingerprint",
                "watch_universe_report_path",
            ),
            Stage4Stage.RESEARCH_INTEGRATION: ("research_run_id",),
            Stage4Stage.OPERATOR_SELECTION: ("selected_opportunity_id",),
            Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN: (
                "selected_opportunity_dry_run_id", "execution_plan_id",
            ),
        }.get(stage, ())
        for key in keys:
            if key in payload:
                updates[key] = payload[key]
        if (
            stage is Stage4Stage.RESEARCH_INTEGRATION
            and "opportunity_ids" in payload
        ):
            updates["selectable_opportunity_ids"] = tuple(
                payload["opportunity_ids"]
            )
        if mark_completed:
            completed = tuple(
                dict.fromkeys(
                    run.completed_stages + (stage,)
                )
            )
            updates["completed_stages"] = completed
        return run.model_copy(update=updates)

    def _build_run(
        self,
        request,
        *,
        prior,
        status,
        started_at=None,
        terminal_reason=None,
        completed_at=None,
        network_calls=None,
    ):
        if started_at is None:
            if prior is None:
                raise ValueError("new Stage 4 run requires started_at")
            started_at = prior.started_at
        values = {
            "portfolio_snapshot_id": None,
            "portfolio_risk_state_id": None,
            "scanner_run_id": None,
            "watch_universe_run_id": None,
            "watch_universe_fingerprint": None,
            "watch_universe_report_path": None,
            "research_run_id": None,
            "selected_opportunity_id": None,
            "selected_opportunity_dry_run_id": None,
            "execution_plan_id": None,
            "selectable_opportunity_ids": (),
            "completed_stages": (),
            "network_calls": 0,
        }
        if prior is not None:
            for key in values:
                values[key] = getattr(prior, key)
        if network_calls is not None:
            values["network_calls"] = network_calls
        identity = {
            "run_id": stage4_run_id(request),
            "request_fingerprint": stage4_request_fingerprint(request),
            "status": status.value,
            "terminal_reason": terminal_reason.value if terminal_reason else None,
            "completed_stages": [item.value for item in values["completed_stages"]],
            "lineage": {key: values[key] for key in values if key != "network_calls"},
            "network_calls": values["network_calls"],
        }
        return Stage4E2ERun(
            run_id=stage4_run_id(request),
            policy_id=request.policy.policy_id,
            policy_version=request.policy.policy_version,
            mode=request.mode,
            status=status,
            terminal_reason=terminal_reason,
            as_of=request.as_of,
            started_at=started_at,
            completed_at=completed_at,
            portfolio_file_fingerprint=request.portfolio_file_fingerprint,
            account_state_id=request.account_state_id,
            request_fingerprint=stage4_request_fingerprint(request),
            fingerprint=canonical_fingerprint(identity),
            **values,
        )

    @staticmethod
    def _input_ids(run):
        return tuple(
            value for value in (
                run.portfolio_file_fingerprint,
                run.account_state_id,
                run.portfolio_snapshot_id,
                run.portfolio_risk_state_id,
                run.scanner_run_id,
                run.watch_universe_run_id,
                run.research_run_id,
                run.selected_opportunity_id,
            ) if value
        )
