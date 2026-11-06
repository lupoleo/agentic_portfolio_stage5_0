from __future__ import annotations

from datetime import datetime, timezone

from app.ai.evidence_provider import EvidenceItem, EvidenceKind
from app.ai.evidence_semantics import (
    EvidenceSemanticDimension as D,
    EvidenceSemanticTaggingService,
    SemanticTaggingMethod,
    deterministic_semantic_dimensions,
)
from app.ai.models import AIResponse
from app.ai.research_service import ResearchEvidence


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def item(text: str, *, kind=EvidenceKind.NEWS) -> EvidenceItem:
    """
    Semantic-tagging unit fixture.

    EvidenceItem's production contract also requires a canonical EvidenceSource.
    These tests are intentionally scoped to EvidenceSemanticTaggingService and
    do not test EvidenceItem/EvidenceSource validation, so model_construct is
    used to avoid fabricating a fake source contract.
    """
    return EvidenceItem.model_construct(
        evidence=ResearchEvidence(
            evidence_id="E1",
            source_type="NEWS_EVENT" if kind == EvidenceKind.NEWS else "MARKET",
            text=text,
            published_at=NOW,
        ),
        source=None,
        kind=kind,
        ticker="SNPS",
        metadata={},
    )


class FakeProvider:
    def __init__(self, dimensions=None):
        self.dimensions = dimensions or []
        self.requests = []

    def infer(self, request):
        self.requests.append(request)
        return AIResponse(
            provider="TEST",
            model="TEST",
            latency_ms=1.0,
            structured_output={
                "dimensions": self.dimensions,
                "confidence": 0.9,
                "rationale": "test",
            },
        )


def test_fundamental_guard():
    dims = deterministic_semantic_dimensions(
        "Revenue rose 42% and non-GAAP EPS reached $3.91."
    )
    assert D.FUNDAMENTAL in dims


def test_analyst_guard():
    dims = deterministic_semantic_dimensions(
        "Analysts raised price targets and earnings estimates."
    )
    assert D.ANALYST_EXPECTATIONS in dims


def test_event_guard():
    dims = deterministic_semantic_dimensions(
        "The company announced a large customer deployment."
    )
    assert D.CATALYST_EVENT in dims


def test_macro_guard_is_narrow():
    assert D.MACRO not in deterministic_semantic_dimensions(
        "Institutions are quietly accumulating shares."
    )
    assert D.MACRO in deterministic_semantic_dimensions(
        "Federal Reserve interest rates remain the key macro driver."
    )


def test_price_guard():
    assert D.PRICE_TECHNICAL in deterministic_semantic_dimensions(
        "The stock rallied 9% after earnings."
    )


def test_market_item_is_deterministic_without_ai_call():
    provider = FakeProvider()
    result = EvidenceSemanticTaggingService(provider).classify(
        item("Latest close 100; RSI14 65.", kind=EvidenceKind.MARKET)
    )
    assert result.dimensions[0] == D.PRICE_TECHNICAL
    assert result.method == SemanticTaggingMethod.DETERMINISTIC_GUARD
    assert provider.requests == []


def test_deterministic_tags_cannot_be_removed_by_ai():
    provider = FakeProvider([])
    result = EvidenceSemanticTaggingService(provider).classify(
        item("Revenue rose 42% and EPS beat consensus.")
    )
    assert D.FUNDAMENTAL in result.dimensions
    assert D.ANALYST_EXPECTATIONS in result.dimensions


def test_ai_can_add_news_context():
    provider = FakeProvider(["NEWS_CONTEXT"])
    result = EvidenceSemanticTaggingService(provider).classify(
        item("UiPath is repositioning its orchestration narrative.")
    )
    assert D.NEWS_CONTEXT in result.dimensions


def test_ai_macro_without_anchor_is_removed():
    provider = FakeProvider(["MACRO", "ANALYST_EXPECTATIONS"])
    result = EvidenceSemanticTaggingService(provider).classify(
        item("Analysts hold the stock while institutions accumulate.")
    )
    assert D.MACRO not in result.dimensions
    assert D.ANALYST_EXPECTATIONS in result.dimensions
    assert "AI_MACRO_REMOVED_WITHOUT_EXPLICIT_MACRO_ANCHOR" in result.warnings


def test_ai_price_without_anchor_is_removed():
    provider = FakeProvider(["PRICE_TECHNICAL"])
    result = EvidenceSemanticTaggingService(provider).classify(
        item("The company announced a strategic partnership.")
    )
    assert D.PRICE_TECHNICAL not in result.ai_dimensions


def test_service_forces_fast_reasoning():
    provider = FakeProvider(["NEWS_CONTEXT"])
    EvidenceSemanticTaggingService(provider).classify(item("Sector narrative."))
    assert provider.requests[0].reasoning_mode.value == "FAST"


def test_source_kind_is_not_mutated():
    x = item("Revenue rose 42%.")
    EvidenceSemanticTaggingService(FakeProvider()).classify(x)
    assert x.kind == EvidenceKind.NEWS


# AI-8C.3b.7 — Evidence Semantic Tagging Reproducibility

class SequentialProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    def infer(self, request):
        self.requests.append(request)
        dimensions = self.outputs.pop(0)
        return AIResponse(
            provider="TEST",
            model="TEST",
            latency_ms=1.0,
            structured_output={
                "dimensions": dimensions,
                "confidence": 0.9,
                "rationale": "test",
            },
        )


def test_ai_catalyst_without_deterministic_anchor_is_removed():
    result = EvidenceSemanticTaggingService(
        FakeProvider(["CATALYST_EVENT"])
    ).classify(item("The company remains a major semiconductor supplier."))
    assert D.CATALYST_EVENT not in result.dimensions
    assert "AI_CATALYST_EVENT_REMOVED_WITHOUT_EXPLICIT_ANCHOR" in result.warnings


def test_ai_fundamental_without_deterministic_anchor_is_removed():
    result = EvidenceSemanticTaggingService(
        FakeProvider(["FUNDAMENTAL"])
    ).classify(item("The company remains a major semiconductor supplier."))
    assert D.FUNDAMENTAL not in result.dimensions


def test_ai_analyst_without_deterministic_anchor_is_removed():
    result = EvidenceSemanticTaggingService(
        FakeProvider(["ANALYST_EXPECTATIONS"])
    ).classify(item("The company remains a major semiconductor supplier."))
    assert D.ANALYST_EXPECTATIONS not in result.dimensions


def test_news_context_has_deterministic_narrative_anchor():
    dims = deterministic_semantic_dimensions(
        "Management commentary focused on competitive positioning and strategy."
    )
    assert D.NEWS_CONTEXT in dims


def test_same_evidence_has_same_canonical_dimensions_despite_ai_variance():
    provider = SequentialProvider(
        [
            ["CATALYST_EVENT", "NEWS_CONTEXT"],
            ["FUNDAMENTAL"],
        ]
    )
    service = EvidenceSemanticTaggingService(provider)
    evidence = item("The company remains a major semiconductor supplier.")

    first = service.classify(evidence)
    second = service.classify(evidence)

    assert first.dimensions == second.dimensions == []
    assert first.ai_dimensions == second.ai_dimensions == []


def test_deterministic_catalyst_is_reproducible_despite_ai_variance():
    provider = SequentialProvider(
        [
            [],
            ["CATALYST_EVENT", "FUNDAMENTAL"],
        ]
    )
    service = EvidenceSemanticTaggingService(provider)
    evidence = item("The company announced a strategic partnership.")

    first = service.classify(evidence)
    second = service.classify(evidence)

    assert first.dimensions == second.dimensions
    assert D.CATALYST_EVENT in first.dimensions
    assert D.NEWS_CONTEXT in first.dimensions


def test_prompt_version_marks_reproducibility_v2():
    result = EvidenceSemanticTaggingService(FakeProvider()).classify(
        item("Revenue rose 10%.")
    )
    assert result.prompt_version == "evidence-semantic-tagging-v2-reproducible"
