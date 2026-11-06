from datetime import datetime, timedelta, timezone

from app.ai.evidence_provider import EvidenceItem, EvidenceKind, EvidenceSource
from app.ai.evidence_quality import (
    EvidenceCoverageDimension,
    EvidenceCoverageLevel,
    EvidenceQualityEvaluator,
)
from app.ai.research_models import EvidenceQuality
from app.ai.research_service import ResearchEvidence

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def item(eid, kind, *, source_id=None, age_days=1, published=True):
    ts = NOW - timedelta(days=age_days) if published else None
    sid = source_id or f"SRC-{eid}"
    return EvidenceItem(
        evidence=ResearchEvidence(
            evidence_id=eid,
            source_type=kind.value,
            text=f"Evidence {eid}",
            published_at=ts,
            metadata={"source_id": sid},
        ),
        source=EvidenceSource(
            source_id=sid,
            provider="TEST",
            source_type=kind.value,
            source_name=f"Source {sid}",
            source_url="https://example.test/item",
            retrieved_at=NOW,
            published_at=ts,
        ),
        kind=kind,
        ticker="PATH",
    )


def test_empty_evidence_is_low_and_explicit():
    a = EvidenceQualityEvaluator().evaluate([], as_of=NOW)
    assert a.quality == EvidenceQuality.LOW
    assert a.coverage_score == 0
    assert "NO_EVIDENCE" in a.warnings


def test_high_quality_does_not_imply_complete_coverage():
    items = [
        item("M1", EvidenceKind.MARKET, source_id="S1"),
        item("N1", EvidenceKind.NEWS, source_id="S2"),
        item("N2", EvidenceKind.NEWS, source_id="S3"),
    ]
    a = EvidenceQualityEvaluator().evaluate(items, as_of=NOW)
    assert a.quality == EvidenceQuality.HIGH
    assert a.coverage_for(EvidenceCoverageDimension.FUNDAMENTAL).level == EvidenceCoverageLevel.NONE
    assert a.coverage_score < 1.0
    assert "NO_FUNDAMENTAL_COVERAGE" in a.warnings


def test_market_and_technical_map_to_same_research_dimension():
    a = EvidenceQualityEvaluator().evaluate([
        item("M1", EvidenceKind.MARKET),
        item("T1", EvidenceKind.TECHNICAL),
    ], as_of=NOW)
    e = a.coverage_for(EvidenceCoverageDimension.PRICE_TECHNICAL)
    assert e.item_count == 2
    assert e.level == EvidenceCoverageLevel.ADEQUATE


def test_news_contributes_context_and_catalyst_event_coverage():
    a = EvidenceQualityEvaluator().evaluate([item("N1", EvidenceKind.NEWS)], as_of=NOW)
    assert a.coverage_for(EvidenceCoverageDimension.NEWS_CONTEXT).item_count == 1
    assert a.coverage_for(EvidenceCoverageDimension.CATALYST_EVENT).item_count == 1


def test_fundamental_is_not_inferred_from_news():
    a = EvidenceQualityEvaluator().evaluate([item("N1", EvidenceKind.NEWS)], as_of=NOW)
    assert a.coverage_for(EvidenceCoverageDimension.FUNDAMENTAL).level == EvidenceCoverageLevel.NONE


def test_three_distinct_fresh_sources_can_reach_high_quality():
    a = EvidenceQualityEvaluator().evaluate([
        item("A", EvidenceKind.MARKET, source_id="S1"),
        item("B", EvidenceKind.FUNDAMENTAL, source_id="S2"),
        item("C", EvidenceKind.ANALYST, source_id="S3"),
    ], as_of=NOW)
    assert a.quality == EvidenceQuality.HIGH
    assert a.unique_sources == 3
    assert a.source_diversity_score == 1.0


def test_single_source_is_penalized_and_warned():
    a = EvidenceQualityEvaluator().evaluate([
        item("A", EvidenceKind.NEWS, source_id="S1"),
        item("B", EvidenceKind.NEWS, source_id="S1"),
    ], as_of=NOW)
    assert a.quality != EvidenceQuality.HIGH
    assert "LOW_SOURCE_DIVERSITY" in a.warnings


def test_stale_evidence_cannot_be_high_quality():
    a = EvidenceQualityEvaluator().evaluate([
        item("A", EvidenceKind.MARKET, source_id="S1", age_days=120),
        # AI-8C.2-R3: fundamentals follow the 135-day reporting window.
        item("B", EvidenceKind.FUNDAMENTAL, source_id="S2", age_days=150),
        item("C", EvidenceKind.ANALYST, source_id="S3", age_days=120),
    ], as_of=NOW)
    assert a.quality != EvidenceQuality.HIGH
    assert a.stale_items == 3
    assert "STALE_EVIDENCE_PRESENT" in a.warnings


def test_undated_evidence_is_visible_not_silently_fresh():
    a = EvidenceQualityEvaluator().evaluate([
        item("A", EvidenceKind.NEWS, source_id="S1", published=False),
    ], as_of=NOW)
    assert a.undated_items == 1
    assert "UNDATED_EVIDENCE_PRESENT" in a.warnings


def test_coverage_levels_are_count_based_and_deterministic():
    items = [item(f"F{i}", EvidenceKind.FUNDAMENTAL, source_id=f"S{i}") for i in range(4)]
    a = EvidenceQualityEvaluator().evaluate(items, as_of=NOW)
    assert a.coverage_for(EvidenceCoverageDimension.FUNDAMENTAL).level == EvidenceCoverageLevel.STRONG


def test_all_research_dimensions_can_be_represented():
    items = [
        item("M", EvidenceKind.MARKET),
        item("F", EvidenceKind.FUNDAMENTAL),
        item("E", EvidenceKind.EVENT),
        item("A", EvidenceKind.ANALYST),
        item("X", EvidenceKind.MACRO),
        item("N", EvidenceKind.NEWS),
    ]
    a = EvidenceQualityEvaluator().evaluate(items, as_of=NOW)
    assert a.coverage_score == 1.0


def test_policy_metadata_makes_quality_coverage_separation_explicit():
    a = EvidenceQualityEvaluator().evaluate([item("M", EvidenceKind.MARKET)], as_of=NOW)
    assert a.metadata["policy"] == "evidence-quality-v2-reporting-cycle"
    assert a.metadata["quality_is_independent_of_coverage"] is True
    assert a.metadata["publisher_reliability_not_inferred"] is True



# --- AI-8C.2-R3: fundamental reporting cycle ------------------------------------


def _fresh_set(fundamental_age_days):
    return [
        item("T", EvidenceKind.TECHNICAL, source_id="S1", age_days=0),
        item("N", EvidenceKind.NEWS, source_id="S2", age_days=2),
        item("A", EvidenceKind.ANALYST, source_id="S3", age_days=0),
        item("F", EvidenceKind.FUNDAMENTAL, source_id="S4", age_days=fundamental_age_days),
    ]


def test_last_reported_quarter_within_window_is_not_stale():
    # Calendar-quarter reporter on 30 September: quarter ended 92 days ago.
    a = EvidenceQualityEvaluator().evaluate(_fresh_set(92), as_of=NOW)
    assert a.stale_items == 0
    assert a.quality == EvidenceQuality.HIGH
    assert "FUNDAMENTAL_REPORTING_PERIOD_STALE" not in a.warnings


def test_quarter_beyond_reporting_window_is_stale():
    a = EvidenceQualityEvaluator().evaluate(_fresh_set(136), as_of=NOW)
    assert a.stale_items == 1
    assert a.quality != EvidenceQuality.HIGH
    assert "FUNDAMENTAL_REPORTING_PERIOD_STALE" in a.warnings


def test_recently_reported_quarter_counts_as_fully_fresh():
    recent = EvidenceQualityEvaluator().evaluate(_fresh_set(30), as_of=NOW)
    older = EvidenceQualityEvaluator().evaluate(_fresh_set(92), as_of=NOW)
    assert recent.freshness_score > older.freshness_score


def test_non_fundamental_evidence_keeps_the_90_day_rule():
    a = EvidenceQualityEvaluator().evaluate([
        item("M", EvidenceKind.MARKET, source_id="S1", age_days=100),
    ], as_of=NOW)
    assert a.stale_items == 1


def test_reporting_window_is_recorded():
    a = EvidenceQualityEvaluator().evaluate(_fresh_set(92), as_of=NOW)
    assert a.metadata["fundamental_reporting_window_days"] == 135
