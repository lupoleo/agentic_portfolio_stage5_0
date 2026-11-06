from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse

from app.ai.evidence_provider import EvidenceItem
from app.ai.evidence_quality import (
    EvidenceCoverageDimension,
    EvidenceCoverageEntry,
    EvidenceCoverageLevel,
    EvidenceQualityAssessment,
    EvidenceQualityEvaluator,
)
from app.ai.evidence_semantics import (
    EvidenceSemanticAssessment,
    EvidenceSemanticDimension,
)


_SEMANTIC_TO_COVERAGE = {
    EvidenceSemanticDimension.PRICE_TECHNICAL:
        EvidenceCoverageDimension.PRICE_TECHNICAL,
    EvidenceSemanticDimension.FUNDAMENTAL:
        EvidenceCoverageDimension.FUNDAMENTAL,
    EvidenceSemanticDimension.CATALYST_EVENT:
        EvidenceCoverageDimension.CATALYST_EVENT,
    EvidenceSemanticDimension.ANALYST_EXPECTATIONS:
        EvidenceCoverageDimension.ANALYST_EXPECTATIONS,
    EvidenceSemanticDimension.MACRO:
        EvidenceCoverageDimension.MACRO,
    EvidenceSemanticDimension.NEWS_CONTEXT:
        EvidenceCoverageDimension.NEWS_CONTEXT,
}


def normalized_source_identity(item: EvidenceItem) -> str:
    """
    Return an independent-source identity suitable for diversity scoring.

    Priority:
      1. publisher-like metadata when present;
      2. hostname/domain from source_url;
      3. stable source/provider name;
      4. provider;
      5. source_id as last-resort fallback.

    Crucially, source_id is NOT the primary identity because Yahoo news can
    create a different source_id for every article.
    """
    source = item.source
    md = {}
    if getattr(source, "metadata", None):
        md.update(source.metadata)
    if getattr(item, "metadata", None):
        md.update(item.metadata)

    for key in ("publisher", "publisher_name", "domain", "source_domain"):
        value = md.get(key)
        if value:
            return str(value).strip().lower()

    url = getattr(source, "source_url", None)
    if url:
        host = urlparse(str(url)).hostname
        if host:
            host = host.lower()
            return host[4:] if host.startswith("www.") else host

    name = getattr(source, "source_name", None)
    if name:
        return str(name).strip().lower()

    provider = getattr(source, "provider", None)
    if provider:
        return str(provider).strip().lower()

    return str(source.source_id).strip().lower()


def semantic_coverage_entries(
    assessments: list[EvidenceSemanticAssessment],
) -> list[EvidenceCoverageEntry]:
    counts = Counter()
    for assessment in assessments:
        for dimension in set(assessment.dimensions):
            counts[_SEMANTIC_TO_COVERAGE[dimension]] += 1

    entries = []
    for dimension in EvidenceCoverageDimension:
        count = counts[dimension]
        if count == 0:
            level = EvidenceCoverageLevel.NONE
        elif count == 1:
            level = EvidenceCoverageLevel.LIMITED
        elif count <= 3:
            level = EvidenceCoverageLevel.ADEQUATE
        else:
            level = EvidenceCoverageLevel.STRONG
        entries.append(
            EvidenceCoverageEntry(
                dimension=dimension,
                level=level,
                item_count=count,
            )
        )
    return entries


class SemanticEvidenceQualityEvaluator:
    """
    Adapter for AI-7D.4C.

    Reuses AI-7D.4A's provenance/freshness quality calculation, but replaces:
      - kind-based coverage with semantic multi-label coverage;
      - article/source-id diversity with normalized publisher/source identity.

    Quality and coverage remain intentionally independent.
    """

    def __init__(self, base: EvidenceQualityEvaluator | None = None) -> None:
        self.base = base or EvidenceQualityEvaluator()

    def evaluate(
        self,
        items: list[EvidenceItem],
        semantic_assessments: list[EvidenceSemanticAssessment],
        *,
        now=None,
    ) -> EvidenceQualityAssessment:
        item_ids = {x.evidence.evidence_id for x in items}
        assessment_ids = {x.evidence_id for x in semantic_assessments}
        missing = item_ids - assessment_ids
        if missing:
            raise ValueError(
           "missing semantic assessments for evidence: "
                + ", ".join(sorted(missing))
            )

        # Propagate the caller-supplied evaluation clock to the base evaluator.
        # This keeps freshness assessment deterministic and reproducible.
        base = self.base.evaluate(
            items,
            as_of=now,
        )

        coverage = semantic_coverage_entries(semantic_assessments)
        represented = sum(
            1 for entry in coverage
            if entry.level != EvidenceCoverageLevel.NONE
        )
        coverage_score = represented / len(EvidenceCoverageDimension)

        identities = {
            normalized_source_identity(item)
            for item in items
        }
        unique_sources = len(identities)
        diversity_score = min(1.0, unique_sources / 3.0) if items else 0.0

        warnings = [
            warning for warning in base.warnings
            if warning not in {
                "LOW_SOURCE_DIVERSITY",
                "NO_FUNDAMENTAL_COVERAGE",
                "NO_PRICE_TECHNICAL_COVERAGE",
            }
        ]
        if items and unique_sources <= 1:
            warnings.append("LOW_SOURCE_DIVERSITY")

        by_dim = {entry.dimension: entry for entry in coverage}
        if by_dim[EvidenceCoverageDimension.FUNDAMENTAL].level == EvidenceCoverageLevel.NONE:
            warnings.append("NO_FUNDAMENTAL_COVERAGE")
        if by_dim[EvidenceCoverageDimension.PRICE_TECHNICAL].level == EvidenceCoverageLevel.NONE:
            warnings.append("NO_PRICE_TECHNICAL_COVERAGE")

        # Recompose quality from explicit, observable assessment fields.
        #
        # 7D.4C must not infer provenance from optional metadata or from the
        # categorical base quality. Instead provenance is computed directly
        # from the canonical EvidenceSource fields available on each item.
        from app.ai.research_models import EvidenceQuality

        def _item_has_provenance(item: EvidenceItem) -> bool:
            source = item.source
            return bool(
                getattr(source, "source_id", None)
                and getattr(source, "provider", None)
            )

        provenance_score = (
            sum(1 for item in items if _item_has_provenance(item)) / len(items)
            if items else 0.0
        )

        observable = (
            0.50 * provenance_score
            + 0.30 * base.freshness_score
            + 0.20 * diversity_score
        )

        if observable >= 0.80 and base.stale_items == 0:
            quality = EvidenceQuality.HIGH
        elif observable >= 0.55:
            quality = EvidenceQuality.MEDIUM
        else:
            quality = EvidenceQuality.LOW
        if unique_sources <= 1 and quality == EvidenceQuality.HIGH:
            quality = EvidenceQuality.MEDIUM

        metadata = dict(base.metadata)
        metadata.update({
            "policy": "evidence-quality-v2-semantic-coverage-reporting-cycle",
            "coverage_basis": "semantic_dimensions",
            "source_diversity_basis": "normalized_publisher_or_domain",
            "normalized_source_identities": sorted(identities),
            "provenance_score": round(provenance_score, 6),
            "observable_quality_score": round(observable, 6),
            "quality_is_independent_of_coverage": True,
        })

        return base.model_copy(update={
            "quality": quality,
            "coverage": coverage,
            "coverage_score": coverage_score,
            "source_diversity_score": diversity_score,
            "unique_sources": unique_sources,
            "warnings": list(dict.fromkeys(warnings)),
            "metadata": metadata,
        })
