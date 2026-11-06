"""Resumable orchestration for E2E-S2.2G."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunMode,
    DryRunStage,
    DryRunStageRecord,
    DryRunStageStatus,
    DryRunStatus,
    DryRunTerminalReason,
    SelectedOpportunityDryRun,
    SelectedOpportunityDryRunRequest,
)


STAGE_ORDER = tuple(DryRunStage)


def _payload(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_payload(value).encode("utf-8")).hexdigest()


def dry_run_id(request: SelectedOpportunityDryRunRequest) -> str:
    identity = request.model_dump(mode="json", exclude={"mode"})
    return "s2g-" + _fingerprint(identity)[:24]


class SelectedOpportunityDryRunService:
    def __init__(self, *, store, executor) -> None:
        self.store = store
        self.executor = executor

    def run(
        self,
        request: SelectedOpportunityDryRunRequest,
        *,
        now: datetime | None = None,
    ) -> SelectedOpportunityDryRun:
        now = now or datetime.now(timezone.utc)
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        run_id = dry_run_id(request)
        request_fp = _fingerprint(request.model_dump(mode="json", exclude={"mode"}))
        existing = self.store.get_run(run_id)

        if existing is not None and existing.request_fingerprint != request_fp:
            raise ValueError("dry-run identity collision with different request")
        if existing is not None and existing.status is DryRunStatus.COMPLETED:
            return existing
        if (
            existing is not None
            and existing.status is DryRunStatus.BLOCKED
            and existing.terminal_reason is not DryRunTerminalReason.CACHE_ONLY_MISS
        ):
            return existing

        if request.mode is DryRunMode.CACHE_ONLY:
            if existing is not None and existing.status in {
                DryRunStatus.COMPLETED,
                DryRunStatus.BLOCKED,
            }:
                return existing
            blocked = self._build_run(
                request,
                run_id=run_id,
                request_fp=request_fp,
                started_at=existing.started_at if existing else now,
                completed_at=now,
                status=DryRunStatus.BLOCKED,
                terminal_reason=DryRunTerminalReason.CACHE_ONLY_MISS,
            )
            self.store.save_run(blocked, request)
            return blocked

        running = self._build_run(
            request,
            run_id=run_id,
            request_fp=request_fp,
            started_at=existing.started_at if existing else now,
            status=DryRunStatus.RUNNING,
        )
        self.store.save_run(running, request)

        for stage in STAGE_ORDER:
            persisted = self.store.get_stage(run_id, stage)
            if persisted is not None and persisted.status is DryRunStageStatus.COMPLETED:
                continue
            started = datetime.now(timezone.utc)
            try:
                result = self.executor.execute(stage, request, run_id)
            except Exception as exc:
                completed = datetime.now(timezone.utc)
                record = DryRunStageRecord(
                    run_id=run_id,
                    stage=stage,
                    status=DryRunStageStatus.FAILED,
                    started_at=started,
                    completed_at=completed,
                    input_ids=self._input_ids(request),
                    input_fingerprint=request_fp,
                    message=f"{type(exc).__name__}: {exc}",
                    diagnostics=(repr(exc),),
                )
                self.store.save_stage(record)
                failed = self._build_run(
                    request,
                    run_id=run_id,
                    request_fp=request_fp,
                    started_at=running.started_at,
                    completed_at=completed,
                    status=DryRunStatus.FAILED,
                    terminal_reason=DryRunTerminalReason.STAGE_FAILED,
                )
                self.store.save_run(failed, request)
                return failed

            if result.stage is not stage:
                raise ValueError(
                    f"stage executor returned {result.stage.value} for {stage.value}"
                )
            completed = datetime.now(timezone.utc)
            record = DryRunStageRecord(
                run_id=run_id,
                stage=stage,
                status=result.status,
                started_at=started,
                completed_at=completed,
                input_ids=self._input_ids(request),
                output_ids=result.output_ids,
                input_fingerprint=request_fp,
                output_fingerprint=_fingerprint(result.output_payload),
                reason=result.reason,
                message=result.message,
                diagnostics=result.diagnostics,
            )
            self.store.save_stage(record)

            if result.status is DryRunStageStatus.BLOCKED:
                blocked = self._build_run(
                    request,
                    run_id=run_id,
                    request_fp=request_fp,
                    started_at=running.started_at,
                    completed_at=completed,
                    status=DryRunStatus.BLOCKED,
                    terminal_reason=result.reason,
                )
                self.store.save_run(blocked, request)
                return blocked
            if result.status is DryRunStageStatus.FAILED:
                failed = self._build_run(
                    request,
                    run_id=run_id,
                    request_fp=request_fp,
                    started_at=running.started_at,
                    completed_at=completed,
                    status=DryRunStatus.FAILED,
                    terminal_reason=DryRunTerminalReason.STAGE_FAILED,
                )
                self.store.save_run(failed, request)
                return failed

        stages = self.store.list_stages(run_id)
        plan_stage = next(value for value in stages if value.stage is DryRunStage.EXECUTION_PLAN)
        plan_id = plan_stage.output_ids[0] if plan_stage.output_ids else None
        completed_run = self._build_run(
            request,
            run_id=run_id,
            request_fp=request_fp,
            started_at=running.started_at,
            completed_at=datetime.now(timezone.utc),
            status=DryRunStatus.COMPLETED,
            terminal_reason=DryRunTerminalReason.DRY_RUN_PLAN_CREATED,
            execution_plan_id=plan_id,
        )
        self.store.save_run(completed_run, request)
        return completed_run

    def _build_run(
        self,
        request,
        *,
        run_id,
        request_fp,
        started_at,
        status,
        completed_at=None,
        terminal_reason=None,
        execution_plan_id=None,
    ):
        completed_stages = tuple(
            value.stage
            for value in self.store.list_stages(run_id)
            if value.status is DryRunStageStatus.COMPLETED
        )
        identity = {
            "run_id": run_id,
            "request_fingerprint": request_fp,
            "completed_stages": [value.value for value in completed_stages],
            "status": status.value,
            "terminal_reason": terminal_reason.value if terminal_reason else None,
            "execution_plan_id": execution_plan_id,
        }
        return SelectedOpportunityDryRun(
            run_id=run_id,
            scanner_research_run_id=request.scanner_research_run_id,
            opportunity_id=request.opportunity_id,
            portfolio_snapshot_id=request.portfolio_snapshot_id,
            policy_id=request.policy.policy_id,
            policy_version=request.policy.policy_version,
            as_of=request.as_of,
            started_at=started_at,
            completed_at=completed_at,
            mode=request.mode,
            status=status,
            terminal_reason=terminal_reason,
            completed_stages=completed_stages,
            execution_plan_id=execution_plan_id,
            request_fingerprint=request_fp,
            fingerprint=_fingerprint(identity),
        )

    @staticmethod
    def _input_ids(request):
        values = [request.scanner_research_run_id, request.portfolio_snapshot_id]
        if request.opportunity_id:
            values.append(request.opportunity_id)
        return tuple(values)
