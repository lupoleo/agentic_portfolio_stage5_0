from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.ai.opportunity_score_models import OpportunityScoringProfile
from app.ai.research_models import EvidenceQuality


STANDARD_WEIGHTS: dict[str, float] = {
    "thesis_score": 0.25,
    "catalyst_score": 0.25,
    "fundamental_score": 0.20,
    "technical_score": 0.15,
    "expectations_score": 0.15,
}

# V1 deliberately keeps the same mathematical weights for EVENT_DRIVEN.
# Event-specific weighting will be introduced only when the event scoring
# dimensions themselves are formalized; the profile is already carried
# through the contract.
PROFILE_WEIGHTS: dict[OpportunityScoringProfile, dict[str, float]] = {
    OpportunityScoringProfile.STANDARD: STANDARD_WEIGHTS,
    OpportunityScoringProfile.EVENT_DRIVEN: STANDARD_WEIGHTS,
}

QUALITY_FACTORS: dict[EvidenceQuality, float] = {
    EvidenceQuality.HIGH: 1.00,
    EvidenceQuality.MEDIUM: 0.75,
    EvidenceQuality.LOW: 0.50,
}


@dataclass(frozen=True)
class OpportunityScoreCalculation:
    raw_score: float | None
    score_confidence: float
    confidence_adjusted_score: float | None
    available_weight: float
    component_completeness: float
    normalized_weights: dict[str, float]


class OpportunityScoreCalculator:
    """Pure deterministic aggregation for semantic opportunity components."""

    def calculate(
        self,
        *,
        component_scores: Mapping[str, float | None],
        evidence_quality: EvidenceQuality,
        evidence_coverage_score: float,
        research_confidence: float,
        scoring_profile: OpportunityScoringProfile = OpportunityScoringProfile.STANDARD,
    ) -> OpportunityScoreCalculation:
        self._validate_unit_interval("evidence_coverage_score", evidence_coverage_score)
        self._validate_unit_interval("research_confidence", research_confidence)

        weights = PROFILE_WEIGHTS[scoring_profile]
        unknown = set(component_scores).difference(weights)
        if unknown:
            raise ValueError(
                "Unknown opportunity score components: " + ", ".join(sorted(unknown))
            )

        supplied = {name: component_scores.get(name) for name in weights}
        for name, value in supplied.items():
            if value is not None and not 0.0 <= value <= 100.0:
                raise ValueError(f"{name} must be between 0 and 100")

        available_weight = sum(
            weights[name] for name, value in supplied.items() if value is not None
        )
        completeness = available_weight / sum(weights.values())

        if available_weight == 0:
            return OpportunityScoreCalculation(
                raw_score=None,
                score_confidence=0.0,
                confidence_adjusted_score=None,
                available_weight=0.0,
                component_completeness=0.0,
                normalized_weights={},
            )

        normalized = {
            name: weights[name] / available_weight
            for name, value in supplied.items()
            if value is not None
        }
        raw = sum(
            supplied[name] * normalized[name]
            for name in normalized
        )

        quality_factor = QUALITY_FACTORS[evidence_quality]

        # Confidence is deterministic and deliberately multiplicative:
        # a severe weakness in any independent reliability dimension cannot
        # be hidden by strength in another dimension.
        confidence = (
            research_confidence
            * quality_factor
            * evidence_coverage_score
            * completeness
        )
        confidence = max(0.0, min(1.0, confidence))

        adjusted = 50.0 + (raw - 50.0) * confidence

        return OpportunityScoreCalculation(
            raw_score=round(raw, 6),
            score_confidence=round(confidence, 6),
            confidence_adjusted_score=round(adjusted, 6),
            available_weight=round(available_weight, 6),
            component_completeness=round(completeness, 6),
            normalized_weights={
                name: round(weight, 6) for name, weight in normalized.items()
            },
        )

    @staticmethod
    def _validate_unit_interval(name: str, value: float) -> None:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1")
