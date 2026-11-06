from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from pydantic import Field

from app.ai.evidence_provider import EvidenceItem, EvidenceKind
from app.ai.models import AIModel
from app.ai.research_models import EvidenceQuality


class EvidenceCoverageDimension(str, Enum):
    PRICE_TECHNICAL = "PRICE_TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    CATALYST_EVENT = "CATALYST_EVENT"
    ANALYST_EXPECTATIONS = "ANALYST_EXPECTATIONS"
    MACRO = "MACRO"
    NEWS_CONTEXT = "NEWS_CONTEXT"


class EvidenceCoverageLevel(str, Enum):
    NONE = "NONE"
    LIMITED = "LIMITED"
    ADEQUATE = "ADEQUATE"
    STRONG = "STRONG"


class EvidenceFreshness(str, Enum):
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class EvidenceCoverageEntry(AIModel):
    dimension: EvidenceCoverageDimension
    level: EvidenceCoverageLevel
    item_count: int = Field(ge=0)


class EvidenceQualityAssessment(AIModel):
    """Deterministic assessment of an evidence set.

    ``quality`` describes the integrity/freshness/diversity of the supplied
    evidence. ``coverage`` describes which research dimensions are actually
    represented.  They are intentionally independent: HIGH-quality evidence
    may still have incomplete coverage.
    """

    quality: EvidenceQuality
    coverage: list[EvidenceCoverageEntry]
    coverage_score: float = Field(ge=0.0, le=1.0)
    freshness_score: float = Field(ge=0.0, le=1.0)
    source_diversity_score: float = Field(ge=0.0, le=1.0)
    total_items: int = Field(ge=0)
    unique_sources: int = Field(ge=0)
    stale_items: int = Field(ge=0)
    undated_items: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    def coverage_for(
        self, dimension: EvidenceCoverageDimension
    ) -> EvidenceCoverageEntry:
        for entry in self.coverage:
            if entry.dimension == dimension:
                return entry
        raise KeyError(dimension)


# AI-8C.2-R3: fundamental evidence is dated by the end of the most recently
# reported quarter. That date is at least one quarter old for most of the
# reporting cycle, so a uniform 90-day staleness rule made HIGH quality
# impossible for calendar-quarter reporters for weeks after each quarter
# end. A reported quarter is current until the next one can be reported:
# one quarter plus a 45-day publication window.
FUNDAMENTAL_REPORTING_WINDOW_DAYS = 135
FUNDAMENTAL_RECENTLY_REPORTED_DAYS = 45
EVIDENCE_QUALITY_POLICY = "evidence-quality-v2-reporting-cycle"


class EvidenceQualityEvaluator:
    """Conservative, deterministic V1 evidence-set calibration.

    No LLM calls are made here.  V1 deliberately avoids pretending to know
    the editorial reliability of arbitrary publishers.  It scores observable
    properties only: provenance, timestamps/freshness and source diversity.
    """

    _DIMENSIONS = tuple(EvidenceCoverageDimension)
    _KIND_TO_DIMENSIONS = {
        EvidenceKind.MARKET: (EvidenceCoverageDimension.PRICE_TECHNICAL,),
        EvidenceKind.TECHNICAL: (EvidenceCoverageDimension.PRICE_TECHNICAL,),
        EvidenceKind.FUNDAMENTAL: (EvidenceCoverageDimension.FUNDAMENTAL,),
        EvidenceKind.EVENT: (EvidenceCoverageDimension.CATALYST_EVENT,),
        EvidenceKind.ANALYST: (EvidenceCoverageDimension.ANALYST_EXPECTATIONS,),
        EvidenceKind.MACRO: (EvidenceCoverageDimension.MACRO,),
        EvidenceKind.NEWS: (
            EvidenceCoverageDimension.NEWS_CONTEXT,
            EvidenceCoverageDimension.CATALYST_EVENT,
        ),
        EvidenceKind.OTHER: (),
    }

    def evaluate(
        self,
        items: Iterable[EvidenceItem],
        *,
        as_of: datetime | None = None,
    ) -> EvidenceQualityAssessment:
        material = list(items)
        now = self._utc(as_of or datetime.now(timezone.utc))

        counts = {dimension: 0 for dimension in self._DIMENSIONS}
        source_ids: set[str] = set()
        stale = 0
        undated = 0
        freshness_points = 0.0
        provenance_complete = 0
        fundamental_period_stale = False
        warnings: list[str] = []

        for item in material:
            for dimension in self._KIND_TO_DIMENSIONS[item.kind]:
                counts[dimension] += 1

            if item.source.source_id:
                source_ids.add(item.source.source_id)
            if item.source.provider and item.source.source_name:
                provenance_complete += 1

            timestamp = item.evidence.published_at or item.source.published_at
            if timestamp is None:
                undated += 1
                freshness_points += 0.35
                continue

            age_days = max(0.0, (now - self._utc(timestamp)).total_seconds() / 86400.0)
            if item.kind is EvidenceKind.FUNDAMENTAL:
                if age_days <= FUNDAMENTAL_RECENTLY_REPORTED_DAYS:
                    freshness_points += 1.0
                elif age_days <= FUNDAMENTAL_REPORTING_WINDOW_DAYS:
                    freshness_points += 0.70
                else:
                    stale += 1
                    freshness_points += 0.10
                    fundamental_period_stale = True
                continue
            if age_days <= 7:
                freshness_points += 1.0
            elif age_days <= 30:
                freshness_points += 0.70
            elif age_days <= 90:
                freshness_points += 0.40
            else:
                stale += 1
                freshness_points += 0.10

        total = len(material)
        freshness_score = freshness_points / total if total else 0.0
        provenance_score = provenance_complete / total if total else 0.0
        diversity_score = min(1.0, len(source_ids) / 3.0) if total else 0.0

        coverage = [
            EvidenceCoverageEntry(
                dimension=dimension,
                level=self._coverage_level(counts[dimension]),
                item_count=counts[dimension],
            )
            for dimension in self._DIMENSIONS
        ]
        covered = sum(1 for count in counts.values() if count > 0)
        coverage_score = covered / len(self._DIMENSIONS)

        if total == 0:
            quality = EvidenceQuality.LOW
            warnings.append("NO_EVIDENCE")
        else:
            observable_quality = (
                0.50 * provenance_score
                + 0.30 * freshness_score
                + 0.20 * diversity_score
            )
            if observable_quality >= 0.80 and stale == 0:
                quality = EvidenceQuality.HIGH
            elif observable_quality >= 0.55:
                quality = EvidenceQuality.MEDIUM
            else:
                quality = EvidenceQuality.LOW

            # A single provenance source cannot support HIGH evidence-set
            # quality, regardless of how fresh or well-attributed its items
            # are.  This is an explicit V1 concentration guard: repeated
            # items from the same source add evidence volume, not independent
            # corroboration.
            if len(source_ids) <= 1 and quality == EvidenceQuality.HIGH:
                quality = EvidenceQuality.MEDIUM

        if undated:
            warnings.append("UNDATED_EVIDENCE_PRESENT")
        if stale:
            warnings.append("STALE_EVIDENCE_PRESENT")
        if fundamental_period_stale:
            warnings.append("FUNDAMENTAL_REPORTING_PERIOD_STALE")
        if total and len(source_ids) <= 1:
            warnings.append("LOW_SOURCE_DIVERSITY")
        if counts[EvidenceCoverageDimension.FUNDAMENTAL] == 0:
            warnings.append("NO_FUNDAMENTAL_COVERAGE")
        if counts[EvidenceCoverageDimension.PRICE_TECHNICAL] == 0:
            warnings.append("NO_PRICE_TECHNICAL_COVERAGE")

        return EvidenceQualityAssessment(
            quality=quality,
            coverage=coverage,
            coverage_score=coverage_score,
            freshness_score=round(freshness_score, 6),
            source_diversity_score=round(diversity_score, 6),
            total_items=total,
            unique_sources=len(source_ids),
            stale_items=stale,
            undated_items=undated,
            warnings=warnings,
            metadata={
                "policy": EVIDENCE_QUALITY_POLICY,
                "fundamental_reporting_window_days": FUNDAMENTAL_REPORTING_WINDOW_DAYS,
                "quality_is_independent_of_coverage": True,
                "publisher_reliability_not_inferred": True,
            },
        )

    @staticmethod
    def _coverage_level(count: int) -> EvidenceCoverageLevel:
        if count <= 0:
            return EvidenceCoverageLevel.NONE
        if count == 1:
            return EvidenceCoverageLevel.LIMITED
        if count <= 3:
            return EvidenceCoverageLevel.ADEQUATE
        return EvidenceCoverageLevel.STRONG

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
