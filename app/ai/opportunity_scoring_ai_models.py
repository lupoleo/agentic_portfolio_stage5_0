from __future__ import annotations

from pydantic import Field, model_validator

from app.ai.models import AIModel


class ComponentAssessment(AIModel):
    score: float | None = Field(default=None, ge=0.0, le=100.0)
    rationale: str | None = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_assessment(self) -> "ComponentAssessment":
        if self.score is None:
            if self.rationale is not None and self.rationale.strip():
                raise ValueError("unscored component cannot contain a rationale")
            if self.supporting_evidence_ids:
                raise ValueError("unscored component cannot cite supporting evidence")
            return self

        if self.rationale is None or not self.rationale.strip():
            raise ValueError("scored component requires a rationale")
        if not self.supporting_evidence_ids:
            raise ValueError("scored component requires supporting evidence")
        if any(not value.strip() for value in self.supporting_evidence_ids):
            raise ValueError("supporting evidence IDs cannot be blank")
        return self


class OpportunityComponentScoringOutput(AIModel):
    thesis: ComponentAssessment
    catalyst: ComponentAssessment
    fundamental: ComponentAssessment
    technical: ComponentAssessment
    expectations: ComponentAssessment

    positive_factors: list[str] = Field(default_factory=list)
    negative_factors: list[str] = Field(default_factory=list)
    uncertainty_factors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_blank_factors(self) -> "OpportunityComponentScoringOutput":
        for values in (
            self.positive_factors,
            self.negative_factors,
            self.uncertainty_factors,
        ):
            if any(not value.strip() for value in values):
                raise ValueError("factor lists cannot contain blank items")
        return self
