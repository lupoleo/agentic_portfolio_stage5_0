"""Versioned contracts for E2E-S2.2G selected-opportunity dry runs."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DryRunModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        use_enum_values=False,
    )


class DryRunMode(str, Enum):
    LIVE = "LIVE"
    CACHE_ONLY = "CACHE_ONLY"


class DryRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class DryRunStage(str, Enum):
    SELECTION_VALIDATION = "SELECTION_VALIDATION"
    OPPORTUNITY_PREPARATION = "OPPORTUNITY_PREPARATION"
    PORTFOLIO_FILTER = "PORTFOLIO_FILTER"
    PORTFOLIO_LIFECYCLE_GATE = "PORTFOLIO_LIFECYCLE_GATE"
    INSTRUMENT_SELECTION = "INSTRUMENT_SELECTION"
    POSITION_SIZING = "POSITION_SIZING"
    TRADE_PROPOSAL = "TRADE_PROPOSAL"
    PORTFOLIO_SIMULATION = "PORTFOLIO_SIMULATION"
    CIO_DECISION = "CIO_DECISION"
    EXECUTION_PLAN = "EXECUTION_PLAN"


class DryRunStageStatus(str, Enum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class DryRunTerminalReason(str, Enum):
    DRY_RUN_PLAN_CREATED = "DRY_RUN_PLAN_CREATED"
    NO_SELECTABLE_OPPORTUNITY = "NO_SELECTABLE_OPPORTUNITY"
    INVALID_SELECTION = "INVALID_SELECTION"
    OPPORTUNITY_NOT_MATERIALIZED = "OPPORTUNITY_NOT_MATERIALIZED"
    PORTFOLIO_SNAPSHOT_MISMATCH = "PORTFOLIO_SNAPSHOT_MISMATCH"
    STALE_PORTFOLIO_SNAPSHOT = "STALE_PORTFOLIO_SNAPSHOT"
    OPERATOR_INPUT_CONFLICT = "OPERATOR_INPUT_CONFLICT"
    CACHE_ONLY_MISS = "CACHE_ONLY_MISS"
    PORTFOLIO_FILTER_BLOCKED = "PORTFOLIO_FILTER_BLOCKED"
    NO_ELIGIBLE_INSTRUMENT = "NO_ELIGIBLE_INSTRUMENT"
    MARKET_INPUT_UNAVAILABLE = "MARKET_INPUT_UNAVAILABLE"
    POSITION_SIZING_BLOCKED = "POSITION_SIZING_BLOCKED"
    SIMULATION_BLOCKED = "SIMULATION_BLOCKED"
    CIO_REJECTED = "CIO_REJECTED"
    CIO_MODIFY_REQUIRED = "CIO_MODIFY_REQUIRED"
    STAGE_FAILED = "STAGE_FAILED"


class SelectedOpportunityDryRunPolicy(DryRunModel):
    policy_id: str = "stage4-v1-selected-opportunity-dry-run"
    policy_version: str = "1"
    require_current_snapshot: bool = True
    require_s2f_provenance: bool = True
    broker_execution_enabled: bool = False
    portfolio_mutation_enabled: bool = False

    @model_validator(mode="after")
    def fail_closed(self):
        if self.broker_execution_enabled or self.portfolio_mutation_enabled:
            raise ValueError("S2.2G cannot enable execution or portfolio mutation")
        if not self.require_current_snapshot:
            raise ValueError("S2.2G requires the current portfolio snapshot")
        if not self.require_s2f_provenance:
            raise ValueError("S2.2G requires persisted S2.2F provenance")
        return self


class SelectedOpportunityParameters(DryRunModel):
    """Explicit operator inputs missing from broker-independent S2.2F output."""

    requested_exposure_eur: float = Field(gt=0)
    max_intended_loss_eur: float = Field(gt=0)
    reference_price: float = Field(gt=0)
    fx_to_eur: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    market_observed_at: datetime
    instrument_id: str | None = None
    entry_type: str = "MARKET"
    entry_price: float | None = Field(default=None, gt=0)
    target_1: float | None = Field(default=None, gt=0)
    target_2: float | None = Field(default=None, gt=0)
    input_source: str = "OPERATOR_DRY_RUN"

    @field_validator("instrument_id", mode="before")
    @classmethod
    def normalize_instrument(cls, value: Any):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("entry_type", "input_source", mode="before")
    @classmethod
    def nonblank(cls, value: Any) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("operator input labels must be nonblank")
        return value

    @model_validator(mode="after")
    def validate_market_input(self):
        if self.market_observed_at.utcoffset() is None:
            raise ValueError("market_observed_at must be timezone-aware")
        if self.stop_price == self.reference_price:
            raise ValueError("stop_price cannot equal reference_price")
        return self


class SelectedOpportunityDryRunRequest(DryRunModel):
    scanner_research_run_id: str
    opportunity_ids: tuple[str, ...]
    portfolio_snapshot_id: str
    as_of: datetime
    mode: DryRunMode = DryRunMode.LIVE
    parameters: SelectedOpportunityParameters | None = None
    policy: SelectedOpportunityDryRunPolicy = Field(
        default_factory=SelectedOpportunityDryRunPolicy
    )

    @field_validator(
        "scanner_research_run_id", "portfolio_snapshot_id", mode="before"
    )
    @classmethod
    def nonblank(cls, value: Any) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("dry-run identity fields must be nonblank")
        return value

    @model_validator(mode="after")
    def validate_request(self):
        if self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        normalized = tuple(
            sorted({str(value).strip() for value in self.opportunity_ids if str(value).strip()})
        )
        if len(normalized) > 1:
            raise ValueError("S2.2G accepts at most one selected opportunity")
        object.__setattr__(self, "opportunity_ids", normalized)
        if self.parameters is not None and self.parameters.market_observed_at > self.as_of:
            raise ValueError("market input cannot be newer than dry-run as_of")
        return self

    @property
    def opportunity_id(self) -> str | None:
        return self.opportunity_ids[0] if self.opportunity_ids else None


class DryRunStageRecord(DryRunModel):
    run_id: str
    stage: DryRunStage
    status: DryRunStageStatus
    started_at: datetime
    completed_at: datetime
    input_ids: tuple[str, ...] = ()
    output_ids: tuple[str, ...] = ()
    input_fingerprint: str
    output_fingerprint: str | None = None
    reason: DryRunTerminalReason | None = None
    message: str
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_record(self):
        for value in (self.started_at, self.completed_at):
            if value.utcoffset() is None:
                raise ValueError("stage timestamps must be timezone-aware")
        if self.completed_at < self.started_at:
            raise ValueError("stage completed_at cannot precede started_at")
        if self.status is DryRunStageStatus.BLOCKED and self.reason is None:
            raise ValueError("blocked stage requires a reason")
        return self


class SelectedOpportunityDryRun(DryRunModel):
    run_id: str
    scanner_research_run_id: str
    opportunity_id: str | None = None
    portfolio_snapshot_id: str
    policy_id: str
    policy_version: str
    as_of: datetime
    started_at: datetime
    completed_at: datetime | None = None
    mode: DryRunMode
    status: DryRunStatus
    terminal_reason: DryRunTerminalReason | None = None
    completed_stages: tuple[DryRunStage, ...] = ()
    execution_plan_id: str | None = None
    dry_run: bool = True
    execution_authorized: bool = False
    broker_orders_submitted: int = 0
    portfolio_mutations: int = 0
    automatic_executions: int = 0
    request_fingerprint: str
    fingerprint: str

    @model_validator(mode="after")
    def validate_run(self):
        for value in (self.as_of, self.started_at):
            if value.utcoffset() is None:
                raise ValueError("run timestamps must be timezone-aware")
        if self.completed_at is not None and self.completed_at.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        if self.status is DryRunStatus.RUNNING and self.completed_at is not None:
            raise ValueError("running dry run cannot have completed_at")
        if self.status in {DryRunStatus.COMPLETED, DryRunStatus.BLOCKED}:
            if self.completed_at is None or self.terminal_reason is None:
                raise ValueError("terminal dry run requires completion and reason")
        if not self.dry_run or self.execution_authorized:
            raise ValueError("S2.2G output must remain non-executable")
        if any((self.broker_orders_submitted, self.portfolio_mutations, self.automatic_executions)):
            raise ValueError("S2.2G cannot record execution side effects")
        return self


class DryRunStageResult(DryRunModel):
    stage: DryRunStage
    status: DryRunStageStatus
    output_ids: tuple[str, ...] = ()
    output_payload: dict[str, Any] = Field(default_factory=dict)
    reason: DryRunTerminalReason | None = None
    message: str
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_result(self):
        if self.status is DryRunStageStatus.BLOCKED and self.reason is None:
            raise ValueError("blocked stage result requires reason")
        return self
