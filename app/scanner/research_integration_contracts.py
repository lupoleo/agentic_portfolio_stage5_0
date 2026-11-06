"""Canonical contracts for E2E-S2.2F Scanner-to-Research integration."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.ai.models import AIModel
from app.ai.opportunity_score_models import OpportunityScore
from app.ai.scan_models import ScanCandidate
from app.scanner.watch_universe_contracts import ResearchSubjectKey


class ResearchHypothesisKind(str, Enum):
    NEW_LONG = "NEW_LONG"
    NEW_SHORT = "NEW_SHORT"
    PORTFOLIO_MONITOR = "PORTFOLIO_MONITOR"
    MEMBER_REVIEW = "MEMBER_REVIEW"


class IntegrationRunStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    RATE_LIMITED = "RATE_LIMITED"


class HypothesisOutcomeStatus(str, Enum):
    PENDING = "PENDING"
    RESEARCHED = "RESEARCHED"
    OPPORTUNITY_CREATED = "OPPORTUNITY_CREATED"
    DEGRADED = "DEGRADED"
    EXCLUDED = "EXCLUDED"
    FAILED = "FAILED"


class HypothesisOutcomeReason(str, Enum):
    PENDING = "PENDING"
    CACHE_ONLY_MISS = "CACHE_ONLY_MISS"
    MONITOR_ONLY = "MONITOR_ONLY"
    OPPORTUNITY_CREATED = "OPPORTUNITY_CREATED"
    MEMBER_NOT_READY = "MEMBER_NOT_READY"
    AMBIGUOUS_MARKET_DATA_IDENTITY = "AMBIGUOUS_MARKET_DATA_IDENTITY"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    RESEARCH_NOT_COMPLETE = "RESEARCH_NOT_COMPLETE"
    ADDITIONAL_RESEARCH_REQUIRED = "ADDITIONAL_RESEARCH_REQUIRED"
    EVIDENCE_QUALITY_LOW = "EVIDENCE_QUALITY_LOW"
    SCORE_NOT_COMPLETE = "SCORE_NOT_COMPLETE"
    SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"
    SCORE_CONFIDENCE_TOO_LOW = "SCORE_CONFIDENCE_TOO_LOW"
    DIRECTIONAL_THESIS_MISSING = "DIRECTIONAL_THESIS_MISSING"
    # AI-8C.3-R2: the score was not computed for the hypothesis direction.
    SCORE_DIRECTION_MISMATCH = "SCORE_DIRECTION_MISMATCH"
    # Temporary: SHORT scoring is not validated yet (AI-8C.3-R2.2 pending).
    SHORT_MATERIALIZATION_SUSPENDED = "SHORT_MATERIALIZATION_SUSPENDED"
    PROCESSING_FAILED = "PROCESSING_FAILED"


class ScannerResearchIntegrationPolicy(AIModel):
    policy_id: str = "stage4-v1-scanner-research-integration"
    policy_version: str = "1"
    minimum_confidence_adjusted_score: float = Field(default=60.0, ge=0, le=100)
    minimum_score_confidence: float = Field(default=0.40, ge=0, le=1)
    market_data_provider: str = "YAHOO_MARKET"
    news_provider: str = "YAHOO_NEWS"
    max_market_items: int = Field(default=20, ge=1, le=200)
    max_news_items: int = Field(default=10, ge=1, le=200)
    default_horizon: str = "SWING"

    @field_validator(
        "policy_id", "policy_version", "market_data_provider",
        "news_provider", "default_horizon", mode="before",
    )
    @classmethod
    def nonblank(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("policy text fields must be nonblank")
        return value.strip()


class ResearchWatchMemberInput(AIModel):
    subject_namespace: str
    subject_value: str
    provenances: tuple[str, ...]
    candidate_keys: tuple[str, ...] = ()
    position_refs: tuple[str, ...] = ()
    yahoo_symbols: tuple[str, ...] = ()
    isins: tuple[str, ...] = ()
    history_routes: tuple[str, ...] = ()
    readiness: str
    diagnostics: tuple[str, ...] = ()

    @property
    def subject_key(self) -> ResearchSubjectKey:
        return ResearchSubjectKey(self.subject_namespace, self.subject_value)

    @model_validator(mode="after")
    def normalize(self):
        object.__setattr__(
            self, "subject_namespace", self.subject_namespace.strip().upper()
        )
        object.__setattr__(
            self, "subject_value", self.subject_value.strip().upper()
        )
        if not self.subject_namespace or not self.subject_value:
            raise ValueError("research subject identity cannot be blank")
        for name in (
            "provenances", "candidate_keys", "position_refs", "yahoo_symbols",
            "isins", "history_routes", "diagnostics",
        ):
            values = tuple(sorted({str(item).strip() for item in getattr(self, name) if str(item).strip()}))
            object.__setattr__(self, name, values)
        if not self.provenances:
            raise ValueError("research member requires provenance")
        object.__setattr__(self, "readiness", self.readiness.strip().upper())
        return self


class ResearchWatchUniverseInput(AIModel):
    run_id: str
    fingerprint: str
    as_of: datetime
    portfolio_snapshot_id: str
    members: tuple[ResearchWatchMemberInput, ...]
    source_report_fingerprint: str | None = None

    @field_validator("run_id", "fingerprint", "portfolio_snapshot_id", mode="before")
    @classmethod
    def nonblank(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("watch-universe identity fields must be nonblank")
        return value.strip()

    @model_validator(mode="after")
    def validate_members(self):
        if self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        keys = [(x.subject_namespace, x.subject_value) for x in self.members]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate Research Watch Universe subject")
        object.__setattr__(
            self,
            "members",
            tuple(sorted(
                self.members,
                key=lambda x: (x.subject_namespace, x.subject_value),
            )),
        )
        return self


class ResearchHypothesis(AIModel):
    hypothesis_id: str
    integration_run_id: str
    watch_universe_run_id: str
    watch_universe_fingerprint: str
    subject_namespace: str
    subject_value: str
    kind: ResearchHypothesisKind
    ticker: str
    candidate: ScanCandidate
    provenances: tuple[str, ...]

    @model_validator(mode="after")
    def validate_identity(self):
        if self.candidate.candidate_id != self.hypothesis_id:
            raise ValueError("candidate_id must equal hypothesis_id")
        if self.candidate.ticker != self.ticker:
            raise ValueError("candidate ticker differs from hypothesis ticker")
        return self


class PersistedEvidenceBundle(AIModel):
    bundle_id: str
    integration_run_id: str
    hypothesis_id: str
    ticker: str
    created_at: datetime
    evidence_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    provider_statuses: tuple[tuple[str, str], ...]
    items: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...] = ()
    fingerprint: str

    @model_validator(mode="after")
    def validate_bundle(self):
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must be unique")
        return self


class ResearchHypothesisOutcome(AIModel):
    integration_run_id: str
    hypothesis_id: str
    subject_namespace: str
    subject_value: str
    kind: ResearchHypothesisKind
    status: HypothesisOutcomeStatus
    reason: HypothesisOutcomeReason
    updated_at: datetime
    evidence_bundle_id: str | None = None
    research_id: str | None = None
    opportunity_score_id: str | None = None
    opportunity_id: str | None = None
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        if self.status is HypothesisOutcomeStatus.OPPORTUNITY_CREATED and not self.opportunity_id:
            raise ValueError("created opportunity outcome requires opportunity_id")
        return self


class ScannerResearchRun(AIModel):
    run_id: str
    watch_universe_run_id: str
    watch_universe_fingerprint: str
    portfolio_snapshot_id: str
    policy: ScannerResearchIntegrationPolicy
    as_of: datetime
    started_at: datetime
    completed_at: datetime | None = None
    status: IntegrationRunStatus
    hypothesis_ids: tuple[str, ...]
    completed_hypothesis_ids: tuple[str, ...] = ()
    opportunity_ids: tuple[str, ...] = ()
    fingerprint: str

    @model_validator(mode="after")
    def validate_run(self):
        for value in (self.as_of, self.started_at):
            if value.utcoffset() is None:
                raise ValueError("run timestamps must be timezone-aware")
        if self.completed_at is not None and self.completed_at.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        if self.status is IntegrationRunStatus.RUNNING and self.completed_at is not None:
            raise ValueError("running integration cannot be completed")
        if self.status is not IntegrationRunStatus.RUNNING and self.completed_at is None:
            raise ValueError("terminal integration requires completed_at")
        return self


class TradeOpportunityProvenanceLink(AIModel):
    opportunity_id: str
    integration_run_id: str
    watch_universe_run_id: str
    watch_universe_fingerprint: str
    subject_namespace: str
    subject_value: str
    hypothesis_id: str
    candidate_id: str
    research_id: str
    opportunity_score_id: str
    portfolio_snapshot_id: str
    evidence_ids: tuple[str, ...]
    inference_ids: tuple[str, ...]
    policy_id: str
    policy_version: str
    invalidation_conditions: tuple[str, ...]
    created_at: datetime

    @model_validator(mode="after")
    def validate_link(self):
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.candidate_id != self.hypothesis_id:
            raise ValueError("candidate_id must match integration hypothesis")
        return self


class OpportunityMaterializationDecision(AIModel):
    eligible: bool
    reason: HypothesisOutcomeReason
    message: str
    score: OpportunityScore | None = None
