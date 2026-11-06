from datetime import datetime, timezone

import pytest

from app.ai.evidence_provider import EvidenceItem, EvidenceKind, EvidenceSource
from app.ai.models import AIResponse, AITask, ReasoningMode, ResponseFormat
from app.ai.news_relevance import (
    NEWS_RELEVANCE_PROMPT_VERSION,
    NewsRelevanceLabel,
    NewsRelevanceMethod,
    NewsRelevanceService,
)
from app.ai.provider import AIModelProvider
from app.ai.research_service import ResearchEvidence

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def item(eid, text, ticker="PATH"):
    return EvidenceItem(
        evidence=ResearchEvidence(
            evidence_id=eid,
            source_type="NEWS_EVENT",
            text=text,
            published_at=NOW,
        ),
        source=EvidenceSource(
            source_id="S-" + eid,
            provider="TEST",
            source_type="NEWS_EVENT",
            source_name="Test",
            retrieved_at=NOW,
            published_at=NOW,
        ),
        kind=EvidenceKind.NEWS,
        ticker=ticker,
    )


class FakeProvider(AIModelProvider):
    def __init__(self, outputs=None):
        self.outputs = list(outputs or [])
        self.requests = []

    @property
    def provider_name(self):
        return "FAKE"

    @property
    def model_name(self):
        return "fake-model"

    def infer(self, request):
        self.requests.append(request)
        payload = self.outputs.pop(0)
        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content="",
            structured_output=payload,
            latency_ms=1.0,
            usage={},
        )


def out(label, confidence=.9, rationale="reason"):
    return {
        "label": label,
        "confidence": confidence,
        "rationale": rationale,
    }


def test_explicit_ticker_is_direct_without_ai_call():
    provider = FakeProvider()
    result = NewsRelevanceService(provider).classify(
        "PATH", [item("E1", "UiPath (PATH) shares rallied.")]
    )
    a = result.assessments[0]
    assert a.label == NewsRelevanceLabel.DIRECT
    assert a.method == NewsRelevanceMethod.DETERMINISTIC_DIRECT_MATCH
    assert provider.requests == []


def test_company_alias_is_direct_without_ai_call():
    provider = FakeProvider()
    svc = NewsRelevanceService(
        provider, company_aliases={"PATH": ["UiPath"]}
    )
    result = svc.classify(
        "PATH", [item("E1", "Why UiPath stock was winning this week.")]
    )
    assert result.assessments[0].label == NewsRelevanceLabel.DIRECT
    assert result.assessments[0].matched_alias == "UiPath"
    assert provider.requests == []


def test_call_level_alias_is_supported():
    provider = FakeProvider()
    result = NewsRelevanceService(provider).classify(
        "PATH",
        [item("E1", "UiPath announced Maestro.")],
        aliases=["UiPath"],
    )
    assert result.assessments[0].label == NewsRelevanceLabel.DIRECT
    assert provider.requests == []


def test_ticker_boundary_does_not_match_pathway():
    provider = FakeProvider([out("IRRELEVANT")])
    result = NewsRelevanceService(provider).classify(
        "PATH", [item("E1", "A new pathway was announced.")]
    )
    assert result.assessments[0].label == NewsRelevanceLabel.IRRELEVANT
    assert len(provider.requests) == 1


def test_non_direct_item_can_be_read_through():
    provider = FakeProvider([
        out("READ_THROUGH", rationale="Concrete peer catalyst mechanism.")
    ])
    result = NewsRelevanceService(provider).classify(
        "PATH", [item("E1", "A peer reported a sector-wide pricing change.")]
    )
    assert result.assessments[0].label == NewsRelevanceLabel.READ_THROUGH
    assert result.assessments[0].method == NewsRelevanceMethod.AI_CLASSIFIER


def test_non_direct_item_can_be_irrelevant():
    provider = FakeProvider([out("IRRELEVANT")])
    result = NewsRelevanceService(provider).classify(
        "PATH", [item("E1", "Workday reported earnings.")]
    )
    assert result.assessments[0].label == NewsRelevanceLabel.IRRELEVANT


def test_ai_direct_after_guard_fails_closed():
    provider = FakeProvider([out("DIRECT")])
    with pytest.raises(ValueError, match="returned DIRECT"):
        NewsRelevanceService(provider).classify(
            "PATH", [item("E1", "Unrelated evidence.")]
        )


def test_fast_typed_json_is_used_only_for_non_direct_items():
    provider = FakeProvider([out("IRRELEVANT")])
    svc = NewsRelevanceService(
        provider, company_aliases={"PATH": ["UiPath"]}
    )
    svc.classify(
        "PATH",
        [
            item("E1", "UiPath announced a product."),
            item("E2", "Workday reported earnings."),
        ],
    )
    assert len(provider.requests) == 1
    req = provider.requests[0]
    assert req.task == AITask.RELEVANCE
    assert req.reasoning_mode == ReasoningMode.FAST
    assert req.response_format == ResponseFormat.JSON
    assert req.output_schema is not None


def test_filter_preserves_order_and_original_evidence():
    provider = FakeProvider([out("IRRELEVANT")])
    direct = item("E1", "UiPath announced Maestro.")
    unrelated = item("E2", "Unrelated company news.")
    before = direct.model_dump(mode="json")
    svc = NewsRelevanceService(
        provider, company_aliases={"PATH": ["UiPath"]}
    )
    kept, _ = svc.filter_relevant("PATH", [direct, unrelated])
    assert [x.evidence.evidence_id for x in kept] == ["E1"]
    assert direct.model_dump(mode="json") == before


def test_prompt_v2_forbids_direct_and_outside_knowledge():
    provider = FakeProvider([out("IRRELEVANT")])
    NewsRelevanceService(provider).classify(
        "PATH", [item("E1", "Workday reported earnings.")]
    )
    prompt = provider.requests[0].prompt
    assert "Never return DIRECT" in prompt
    assert "outside knowledge" in prompt
    assert NEWS_RELEVANCE_PROMPT_VERSION == "news-relevance-v3-canonical-order"


def test_ticker_mismatch_rejected_before_ai():
    provider = FakeProvider()
    with pytest.raises(ValueError, match="ticker must match"):
        NewsRelevanceService(provider).classify(
            "PATH", [item("E1", "x", ticker="NVDA")]
        )


def test_empty_input_uses_no_ai():
    provider = FakeProvider()
    result = NewsRelevanceService(provider).classify("PATH", [])
    assert result.assessments == []
    assert provider.requests == []
