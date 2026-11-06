from types import SimpleNamespace

import pytest

from app.ai.evidence_provider import EvidenceItem, EvidenceKind
from app.ai.research_models import EvidenceQuality
from app.ai.research_service import ResearchEvidence, ResearchService


def candidate(ticker="SNPS"):
    return SimpleNamespace(ticker=ticker)


def research_evidence(eid):
    return ResearchEvidence(
        evidence_id=eid,
        source_type="NEWS_EVENT",
        text="Revenue rose after quarterly earnings.",
    )


def evidence_item(eid, ticker="SNPS"):
    ev = research_evidence(eid)
    return EvidenceItem.model_construct(
        evidence=ev,
        source=SimpleNamespace(
            source_id=f"SRC-{eid}",
            provider="TEST",
            source_name="Reuters",
            source_url="https://reuters.com/article",
            metadata={},
        ),
        kind=EvidenceKind.NEWS,
        ticker=ticker,
        metadata={},
    )


def test_evidence_items_must_match_research_evidence_exactly():
    with pytest.raises(ValueError, match="correspond exactly"):
        ResearchService._validate_evidence_items(
            candidate(),
            [research_evidence("E1"), research_evidence("E2")],
            [evidence_item("E1")],
        )


def test_evidence_items_reject_duplicate_ids():
    with pytest.raises(ValueError, match="duplicate evidence_id"):
        ResearchService._validate_evidence_items(
            candidate(),
            [research_evidence("E1")],
            [evidence_item("E1"), evidence_item("E1")],
        )


def test_evidence_items_ticker_must_match_candidate():
    with pytest.raises(ValueError, match="ticker must match"):
        ResearchService._validate_evidence_items(
            candidate("SNPS"),
            [research_evidence("E1")],
            [evidence_item("E1", ticker="PATH")],
        )


def test_evidence_quality_report_is_serializable_and_explicit():
    from app.ai.evidence_quality import (
        EvidenceCoverageDimension,
        EvidenceCoverageEntry,
        EvidenceCoverageLevel,
        EvidenceQualityAssessment,
    )

    assessment = EvidenceQualityAssessment(
        quality=EvidenceQuality.HIGH,
        coverage=[
            EvidenceCoverageEntry(
                dimension=EvidenceCoverageDimension.FUNDAMENTAL,
                level=EvidenceCoverageLevel.ADEQUATE,
                item_count=2,
            )
        ],
        coverage_score=1 / 6,
        freshness_score=1.0,
        source_diversity_score=2 / 3,
        total_items=2,
        unique_sources=2,
        stale_items=0,
        undated_items=0,
        warnings=[],
        metadata={"policy": "evidence-quality-v1-semantic-coverage"},
    )
    report = ResearchService._evidence_quality_report_dict(assessment)
    assert report["quality"] == "HIGH"
    assert report["unique_sources"] == 2
    assert report["coverage"][0]["dimension"] == "FUNDAMENTAL"
    assert report["coverage"][0]["level"] == "ADEQUATE"
    assert report["metadata"]["policy"] == "evidence-quality-v1-semantic-coverage"
