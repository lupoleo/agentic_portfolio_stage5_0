from datetime import datetime, timezone

from app.ai.evidence_provider import EvidenceItem, EvidenceKind
from app.ai.evidence_quality import EvidenceCoverageDimension, EvidenceCoverageLevel
from app.ai.evidence_quality_semantic import (
    SemanticEvidenceQualityEvaluator,
    normalized_source_identity,
    semantic_coverage_entries,
)
from app.ai.evidence_semantics import (
    EvidenceSemanticAssessment,
    EvidenceSemanticDimension as D,
    SemanticTaggingMethod,
)
from app.ai.research_service import ResearchEvidence


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class SourceStub:
    def __init__(self, source_id, provider="YAHOO", source_name=None,
                 source_url=None, metadata=None):
        self.source_id = source_id
        self.provider = provider
        self.source_name = source_name
        self.source_url = source_url
        self.metadata = metadata or {}


def item(eid, source, text="text", kind=EvidenceKind.NEWS):
    return EvidenceItem.model_construct(
        evidence=ResearchEvidence(
            evidence_id=eid,
            source_type="NEWS_EVENT",
            text=text,
            published_at=NOW,
        ),
        source=source,
        kind=kind,
        ticker="SNPS",
        metadata={},
    )


def sem(eid, dims):
    return EvidenceSemanticAssessment(
        evidence_id=eid,
        ticker="SNPS",
        dimensions=dims,
        deterministic_dimensions=dims,
        ai_dimensions=[],
        confidence=1.0,
        method=SemanticTaggingMethod.DETERMINISTIC_GUARD,
        rationale="test",
    )


def test_domain_beats_per_article_source_id():
    a = item("E1", SourceStub("article-1", source_url="https://www.reuters.com/a"))
    b = item("E2", SourceStub("article-2", source_url="https://www.reuters.com/b"))
    assert normalized_source_identity(a) == "reuters.com"
    assert normalized_source_identity(b) == "reuters.com"


def test_publisher_metadata_has_priority():
    x = item("E1", SourceStub(
        "x", source_url="https://finance.yahoo.com/x",
        metadata={"publisher": "Reuters"},
    ))
    assert normalized_source_identity(x) == "reuters"


def test_semantic_multilabel_counts_each_dimension():
    entries = semantic_coverage_entries([
        sem("E1", [D.FUNDAMENTAL, D.CATALYST_EVENT]),
        sem("E2", [D.FUNDAMENTAL, D.ANALYST_EXPECTATIONS]),
    ])
    by = {x.dimension: x for x in entries}
    assert by[EvidenceCoverageDimension.FUNDAMENTAL].item_count == 2
    assert by[EvidenceCoverageDimension.CATALYST_EVENT].item_count == 1
    assert by[EvidenceCoverageDimension.ANALYST_EXPECTATIONS].item_count == 1


def test_coverage_levels():
    entries = semantic_coverage_entries([
        sem("E1", [D.FUNDAMENTAL]),
        sem("E2", [D.FUNDAMENTAL]),
        sem("E3", [D.FUNDAMENTAL]),
        sem("E4", [D.FUNDAMENTAL]),
    ])
    by = {x.dimension: x for x in entries}
    assert by[EvidenceCoverageDimension.FUNDAMENTAL].level == EvidenceCoverageLevel.STRONG
    assert by[EvidenceCoverageDimension.MACRO].level == EvidenceCoverageLevel.NONE


def test_missing_semantic_assessment_fails_closed():
    x = item("E1", SourceStub("x", source_url="https://reuters.com/a"))
    try:
        SemanticEvidenceQualityEvaluator().evaluate([x], [], now=NOW)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "missing semantic assessments" in str(exc)


def test_same_publisher_is_one_independent_source():
    items = [
        item("E1", SourceStub("a", source_url="https://reuters.com/a")),
        item("E2", SourceStub("b", source_url="https://reuters.com/b")),
    ]
    result = SemanticEvidenceQualityEvaluator().evaluate(
        items,
        [sem("E1", [D.FUNDAMENTAL]), sem("E2", [D.CATALYST_EVENT])],
        now=NOW,
    )
    assert result.unique_sources == 1
    assert result.source_diversity_score < 1.0
    assert "LOW_SOURCE_DIVERSITY" in result.warnings


def test_three_publishers_reach_full_diversity():
    items = [
        item("E1", SourceStub("a", source_url="https://reuters.com/a")),
        item("E2", SourceStub("b", source_url="https://bloomberg.com/b")),
        item("E3", SourceStub("c", source_url="https://wsj.com/c")),
    ]
    result = SemanticEvidenceQualityEvaluator().evaluate(
        items,
        [sem("E1", [D.FUNDAMENTAL]), sem("E2", [D.CATALYST_EVENT]),
         sem("E3", [D.ANALYST_EXPECTATIONS])],
        now=NOW,
    )
    assert result.unique_sources == 3
    assert result.source_diversity_score == 1.0


def test_semantic_coverage_replaces_kind_coverage():
    items = [
        item("E1", SourceStub("a", source_url="https://reuters.com/a")),
        item("E2", SourceStub("b", source_url="https://bloomberg.com/b")),
    ]
    result = SemanticEvidenceQualityEvaluator().evaluate(
        items,
        [sem("E1", [D.FUNDAMENTAL]), sem("E2", [D.ANALYST_EXPECTATIONS])],
        now=NOW,
    )
    assert result.coverage_for(EvidenceCoverageDimension.FUNDAMENTAL).item_count == 1
    assert result.coverage_for(EvidenceCoverageDimension.NEWS_CONTEXT).item_count == 0


def test_quality_and_coverage_remain_independent():
    items = [
        item("E1", SourceStub("a", source_url="https://reuters.com/a")),
        item("E2", SourceStub("b", source_url="https://bloomberg.com/b")),
        item("E3", SourceStub("c", source_url="https://wsj.com/c")),
    ]
    result = SemanticEvidenceQualityEvaluator().evaluate(
        items,
        [sem("E1", [D.FUNDAMENTAL]), sem("E2", [D.FUNDAMENTAL]),
         sem("E3", [D.FUNDAMENTAL])],
        now=NOW,
    )
    assert result.coverage_score < 1.0
    assert result.metadata["quality_is_independent_of_coverage"] is True


from app.ai.research_models import EvidenceQuality


def test_fix2_does_not_depend_on_observable_quality_metadata():
    items = [
        item("E1", SourceStub("a", source_url="https://reuters.com/a")),
        item("E2", SourceStub("b", source_url="https://bloomberg.com/b")),
        item("E3", SourceStub("c", source_url="https://wsj.com/c")),
    ]
    result = SemanticEvidenceQualityEvaluator().evaluate(
        items,
        [sem("E1", [D.FUNDAMENTAL]),
         sem("E2", [D.CATALYST_EVENT]),
         sem("E3", [D.ANALYST_EXPECTATIONS])],
        now=NOW,
    )
    assert result.metadata["source_diversity_basis"] == "normalized_publisher_or_domain"
    assert result.source_diversity_score == 1.0
    # The semantic adapter must not collapse a healthy base assessment to LOW
    # merely because an optional metadata score is absent.
    assert result.quality != EvidenceQuality.LOW


def test_fix2_same_publisher_can_downgrade_high_but_not_fake_independence():
    items = [
        item("E1", SourceStub("a", source_url="https://reuters.com/a")),
        item("E2", SourceStub("b", source_url="https://reuters.com/b")),
        item("E3", SourceStub("c", source_url="https://reuters.com/c")),
    ]
    result = SemanticEvidenceQualityEvaluator().evaluate(
        items,
        [sem("E1", [D.FUNDAMENTAL]),
         sem("E2", [D.CATALYST_EVENT]),
         sem("E3", [D.ANALYST_EXPECTATIONS])],
        now=NOW,
    )
    assert result.unique_sources == 1
    assert result.quality != EvidenceQuality.HIGH
    assert "LOW_SOURCE_DIVERSITY" in result.warnings


def test_fix3_quality_components_are_explicit():
    items = [
        item("E1", SourceStub("a", source_url="https://reuters.com/a")),
        item("E2", SourceStub("b", source_url="https://bloomberg.com/b")),
        item("E3", SourceStub("c", source_url="https://wsj.com/c")),
    ]
    result = SemanticEvidenceQualityEvaluator().evaluate(
        items,
        [sem("E1", [D.FUNDAMENTAL]),
         sem("E2", [D.CATALYST_EVENT]),
         sem("E3", [D.ANALYST_EXPECTATIONS])],
        now=NOW,
    )
    assert result.metadata["provenance_score"] == 1.0
    assert result.freshness_score == 1.0
    assert result.source_diversity_score == 1.0
    assert result.metadata["observable_quality_score"] == 1.0
    assert result.quality == EvidenceQuality.HIGH
