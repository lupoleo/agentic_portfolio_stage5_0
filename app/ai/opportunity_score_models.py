from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import Field, field_validator, model_validator
from app.ai.models import AIModel
from app.ai.research_models import EvidenceQuality

class OpportunityScoringProfile(str, Enum):
    STANDARD="STANDARD"
    EVENT_DRIVEN="EVENT_DRIVEN"

class OpportunityScoringStatus(str, Enum):
    SCORED="SCORED"
    PARTIAL="PARTIAL"
    NOT_SCORABLE="NOT_SCORABLE"

class OpportunityScore(AIModel):
    """Standalone opportunity assessment; deliberately excludes portfolio fit and CIO state."""
    opportunity_score_id: str
    candidate_id: str
    research_id: str
    scan_id: str
    created_at: datetime
    ticker: str
    scoring_profile: OpportunityScoringProfile=OpportunityScoringProfile.STANDARD
    scoring_status: OpportunityScoringStatus
    thesis_score: float|None=Field(default=None,ge=0,le=100)
    catalyst_score: float|None=Field(default=None,ge=0,le=100)
    fundamental_score: float|None=Field(default=None,ge=0,le=100)
    technical_score: float|None=Field(default=None,ge=0,le=100)
    expectations_score: float|None=Field(default=None,ge=0,le=100)
    raw_score: float|None=Field(default=None,ge=0,le=100)
    score_confidence: float=Field(ge=0,le=1)
    confidence_adjusted_score: float|None=Field(default=None,ge=0,le=100)
    evidence_quality: EvidenceQuality
    evidence_coverage_score: float=Field(ge=0,le=1)
    research_confidence: float=Field(ge=0,le=1)
    positive_factors: list[str]=Field(default_factory=list)
    negative_factors: list[str]=Field(default_factory=list)
    uncertainty_factors: list[str]=Field(default_factory=list)
    requires_additional_research: bool=False
    evidence_ids: list[str]=Field(default_factory=list)
    inference_ids: list[str]=Field(default_factory=list)
    portfolio_snapshot_id: str|None=None
    risk_state_id: str|None=None
    metadata: dict[str,Any]=Field(default_factory=dict)

    @field_validator("ticker",mode="before")
    @classmethod
    def normalize_ticker(cls,v):
        if not isinstance(v,str): return v
        v=v.strip().upper()
        if not v: raise ValueError("ticker cannot be blank")
        return v

    @field_validator("opportunity_score_id","candidate_id","research_id","scan_id",mode="before")
    @classmethod
    def reject_blank_ids(cls,v):
        if not isinstance(v,str): return v
        v=v.strip()
        if not v: raise ValueError("identifier cannot be blank")
        return v

    @field_validator("positive_factors","negative_factors","uncertainty_factors","evidence_ids","inference_ids")
    @classmethod
    def reject_blank_list_items(cls,values):
        if any(not v.strip() for v in values): raise ValueError("list items cannot be blank")
        return values

    @model_validator(mode="after")
    def validate_scoring_state(self):
        components=(self.thesis_score,self.catalyst_score,self.fundamental_score,self.technical_score,self.expectations_score)
        available=sum(v is not None for v in components)
        if self.scoring_status==OpportunityScoringStatus.NOT_SCORABLE:
            if self.raw_score is not None or self.confidence_adjusted_score is not None:
                raise ValueError("NOT_SCORABLE cannot contain aggregate opportunity scores")
            if not self.requires_additional_research:
                raise ValueError("NOT_SCORABLE requires requires_additional_research=True")
            return self
        if available==0: raise ValueError("scorable opportunity requires at least one component score")
        if self.raw_score is None or self.confidence_adjusted_score is None:
            raise ValueError("scorable opportunity requires aggregate scores")
        if self.scoring_status==OpportunityScoringStatus.SCORED and available<5:
            raise ValueError("SCORED requires all five component scores")
        if self.scoring_status==OpportunityScoringStatus.SCORED and self.requires_additional_research:
            raise ValueError("SCORED cannot require additional research")
        if self.scoring_status==OpportunityScoringStatus.PARTIAL and not self.requires_additional_research:
            raise ValueError("PARTIAL requires requires_additional_research=True")
        return self
