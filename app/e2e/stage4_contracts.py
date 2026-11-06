"""Immutable contracts for E2E-S4.0A end-to-end orchestration."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Stage4Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        use_enum_values=False,
    )


class Stage4Mode(str, Enum):
    LIVE = "LIVE"
    PREFER_CACHE = "PREFER_CACHE"
    CACHE_ONLY = "CACHE_ONLY"


class Stage4RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_OPERATOR_SELECTION = "WAITING_FOR_OPERATOR_SELECTION"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class Stage4Stage(str, Enum):
    PORTFOLIO_ANALYSIS = "PORTFOLIO_ANALYSIS"
    SCANNER_DISCOVERY = "SCANNER_DISCOVERY"
    WATCH_UNIVERSE_ASSEMBLY = "WATCH_UNIVERSE_ASSEMBLY"
    RESEARCH_INTEGRATION = "RESEARCH_INTEGRATION"
    OPERATOR_SELECTION = "OPERATOR_SELECTION"
    SELECTED_OPPORTUNITY_DRY_RUN = "SELECTED_OPPORTUNITY_DRY_RUN"
    FINAL_EVIDENCE = "FINAL_EVIDENCE"


class Stage4StageStatus(str, Enum):
    COMPLETED = "COMPLETED"
    WAITING_FOR_OPERATOR_SELECTION = "WAITING_FOR_OPERATOR_SELECTION"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class Stage4TerminalReason(str, Enum):
    E2E_DRY_RUN_COMPLETED = "E2E_DRY_RUN_COMPLETED"
    WAITING_FOR_OPERATOR_SELECTION = "WAITING_FOR_OPERATOR_SELECTION"
    NO_SELECTABLE_OPPORTUNITY = "NO_SELECTABLE_OPPORTUNITY"
    UPSTREAM_BLOCKED = "UPSTREAM_BLOCKED"
    UPSTREAM_PARTIAL = "UPSTREAM_PARTIAL"
    LINEAGE_MISMATCH = "LINEAGE_MISMATCH"
    CACHE_ONLY_MISS = "CACHE_ONLY_MISS"
    DOWNSTREAM_BLOCKED = "DOWNSTREAM_BLOCKED"
    SAFETY_INVARIANT_VIOLATION = "SAFETY_INVARIANT_VIOLATION"
    STAGE_FAILED = "STAGE_FAILED"


TERMINAL_RUN_STATUSES = frozenset(
    {
        Stage4RunStatus.WAITING_FOR_OPERATOR_SELECTION,
        Stage4RunStatus.COMPLETED,
        Stage4RunStatus.BLOCKED,
        Stage4RunStatus.PARTIAL,
        Stage4RunStatus.FAILED,
    }
)

IMMUTABLE_RUN_STATUSES = frozenset(
    {
        Stage4RunStatus.WAITING_FOR_OPERATOR_SELECTION,
        Stage4RunStatus.COMPLETED,
        Stage4RunStatus.BLOCKED,
        Stage4RunStatus.FAILED,
    }
)


def canonical_payload(value: Any) -> str:
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


def canonical_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_payload(value).encode("utf-8")).hexdigest()


class Stage4E2EPolicy(Stage4Model):
    policy_id: str = "stage4-v1-first-complete-e2e"
    policy_version: str = "1"
    preserve_existing_contracts: bool = True
    require_explicit_operator_selection: bool = True
    allow_batch_allocation: bool = False
    broker_execution_enabled: bool = False
    portfolio_mutation_enabled: bool = False

    @model_validator(mode="after")
    def frozen_safety_policy(self):
        if not self.preserve_existing_contracts:
            raise ValueError("E2E-S4.0A must preserve existing contracts")
        if not self.require_explicit_operator_selection:
            raise ValueError("E2E-S4.0A requires explicit operator selection")
        if self.allow_batch_allocation:
            raise ValueError("E2E-S4.0A does not support batch allocation")
        if self.broker_execution_enabled or self.portfolio_mutation_enabled:
            raise ValueError("E2E-S4.0A cannot enable execution or mutation")
        return self


class Stage4E2ERequest(Stage4Model):
    portfolio_file: str
    portfolio_file_fingerprint: str
    account_state_id: str
    scanner_configuration_id: str
    scanner_configuration_fingerprint: str
    as_of: datetime
    mode: Stage4Mode = Stage4Mode.PREFER_CACHE
    selected_opportunity_ids: tuple[str, ...] = ()
    policy: Stage4E2EPolicy = Field(default_factory=Stage4E2EPolicy)

    @field_validator(
        "portfolio_file",
        "portfolio_file_fingerprint",
        "account_state_id",
        "scanner_configuration_id",
        "scanner_configuration_fingerprint",
        mode="before",
    )
    @classmethod
    def nonblank(cls, value: Any) -> str:
        result = str(value).strip()
        if not result:
            raise ValueError("Stage 4 identity fields must be nonblank")
        return result

    @model_validator(mode="after")
    def validate_request(self):
        if self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        selected = tuple(
            sorted(
                {
                    str(value).strip()
                    for value in self.selected_opportunity_ids
                    if str(value).strip()
                }
            )
        )
        if len(selected) > 1:
            raise ValueError("E2E-S4.0A accepts at most one selected opportunity")
        object.__setattr__(self, "selected_opportunity_ids", selected)
        return self

    @property
    def selected_opportunity_id(self) -> str | None:
        return self.selected_opportunity_ids[0] if self.selected_opportunity_ids else None


def stage4_request_fingerprint(request: Stage4E2ERequest) -> str:
    return canonical_fingerprint(request.model_dump(mode="json", exclude={"mode"}))


def stage4_run_id(request: Stage4E2ERequest) -> str:
    return "s4a-" + stage4_request_fingerprint(request)[:24]


class Stage4StageRecord(Stage4Model):
    run_id: str
    stage: Stage4Stage
    status: Stage4StageStatus
    started_at: datetime
    completed_at: datetime
    input_ids: tuple[str, ...] = ()
    output_ids: tuple[str, ...] = ()
    input_fingerprint: str
    output_fingerprint: str | None = None
    output_payload: dict[str, Any] = Field(default_factory=dict)
    reason: Stage4TerminalReason | None = None
    message: str
    diagnostics: tuple[str, ...] = ()
    network_calls: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_record(self):
        if self.started_at.utcoffset() is None or self.completed_at.utcoffset() is None:
            raise ValueError("stage timestamps must be timezone-aware")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.status is not Stage4StageStatus.COMPLETED and self.reason is None:
            raise ValueError("non-completed stage requires a terminal reason")
        return self


class Stage4StageResult(Stage4Model):
    stage: Stage4Stage
    status: Stage4StageStatus
    output_ids: tuple[str, ...] = ()
    output_payload: dict[str, Any] = Field(default_factory=dict)
    reason: Stage4TerminalReason | None = None
    message: str
    diagnostics: tuple[str, ...] = ()
    network_calls: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_result(self):
        if self.status is not Stage4StageStatus.COMPLETED and self.reason is None:
            raise ValueError("non-completed stage result requires a terminal reason")
        return self


class Stage4E2ERun(Stage4Model):
    run_id: str
    checkpoint: str = "E2E-S4.0A"
    policy_id: str
    policy_version: str
    mode: Stage4Mode
    status: Stage4RunStatus
    terminal_reason: Stage4TerminalReason | None = None
    as_of: datetime
    started_at: datetime
    completed_at: datetime | None = None
    portfolio_file_fingerprint: str
    portfolio_snapshot_id: str | None = None
    portfolio_risk_state_id: str | None = None
    account_state_id: str
    scanner_run_id: str | None = None
    watch_universe_run_id: str | None = None
    watch_universe_fingerprint: str | None = None
    watch_universe_report_path: str | None = None
    research_run_id: str | None = None
    selected_opportunity_id: str | None = None
    selected_opportunity_dry_run_id: str | None = None
    execution_plan_id: str | None = None
    selectable_opportunity_ids: tuple[str, ...] = ()
    completed_stages: tuple[Stage4Stage, ...] = ()
    dry_run: bool = True
    execution_authorized: bool = False
    broker_orders_submitted: int = 0
    portfolio_mutations: int = 0
    automatic_executions: int = 0
    network_calls: int = Field(default=0, ge=0)
    request_fingerprint: str
    fingerprint: str

    @model_validator(mode="after")
    def validate_run(self):
        for value in (self.as_of, self.started_at):
            if value.utcoffset() is None:
                raise ValueError("run timestamps must be timezone-aware")
        if self.completed_at is not None and self.completed_at.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        if self.status in TERMINAL_RUN_STATUSES:
            if self.completed_at is None or self.terminal_reason is None:
                raise ValueError("terminal run requires completion and reason")
        elif self.completed_at is not None or self.terminal_reason is not None:
            raise ValueError("non-terminal run cannot have terminal fields")
        if self.status is Stage4RunStatus.COMPLETED and not self.execution_plan_id:
            raise ValueError("completed E2E run requires an execution plan")
        selectable = tuple(sorted({value.strip() for value in self.selectable_opportunity_ids if value.strip()}))
        object.__setattr__(self, "selectable_opportunity_ids", selectable)
        if not self.dry_run or self.execution_authorized:
            raise ValueError("E2E-S4.0A must remain a non-authorized dry run")
        if any(
            (
                self.broker_orders_submitted,
                self.portfolio_mutations,
                self.automatic_executions,
            )
        ):
            raise ValueError("E2E-S4.0A cannot record execution side effects")
        return self
