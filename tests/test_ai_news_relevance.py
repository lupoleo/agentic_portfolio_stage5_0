from datetime import datetime, timezone

import pytest

from app.ai.evidence_provider import EvidenceItem, EvidenceKind, EvidenceSource
from app.ai.models import AIResponse, ReasoningMode, ResponseFormat, AITask
from app.ai.news_relevance import (
    NEWS_RELEVANCE_PROMPT_VERSION,
    NewsRelevanceLabel,
    NewsRelevanceMethod,
    NewsRelevanceService,
)
from app.ai.provider import AIModelProvider
from app.ai.research_service import ResearchEvidence

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def item(evidence_id="E1", text="UiPath announced a product update."):
    return EvidenceItem(
        evidence=ResearchEvidence(
            evidence_id=evidence_id,
            source_type="NEWS_EVENT",
            text=text,
            published_at=NOW,
        ),
        source=EvidenceSource(
            source_id="S-" + evidence_id,
            provider="TEST",
            source_type="NEWS_EVENT",
            source_name="Test",
            retrieved_at=NOW,
            published_at=NOW,
        ),
        kind=EvidenceKind.NEWS,
        ticker="PATH",
    )


class FakeProvider(AIModelProvider):
    def __init__(self, outputs):
        self.outputs = list(outputs)
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


def out(label, confidence=.9, rationale="grounded rationale"):
    return {
        "label": label,
        "confidence": confidence,
        "rationale": rationale,
    }


def path_service(provider):
    return NewsRelevanceService(
        provider,
        company_aliases={"PATH": ["UiPath"]},
    )


def test_direct_classification():
    provider = FakeProvider([])
    result = path_service(provider).classify("PATH", [item()])
    assessment = result.assessments[0]
    assert assessment.label == NewsRelevanceLabel.DIRECT
    assert assessment.method == NewsRelevanceMethod.DETERMINISTIC_DIRECT_MATCH
    assert assessment.matched_alias == "UiPath"
    assert provider.requests == []


def test_read_through_classification():
    provider = FakeProvider([
        out("READ_THROUGH", rationale="Peer results affect sector expectations.")
    ])
    result = path_service(provider).classify(
        "PATH",
        [item(text="Peer software results changed sector expectations.")],
    )
    assert result.assessments[0].label == NewsRelevanceLabel.READ_THROUGH
    assert result.assessments[0].method == NewsRelevanceMethod.AI_CLASSIFIER


def test_irrelevant_classification():
    provider = FakeProvider([out("IRRELEVANT")])
    result = path_service(provider).classify(
        "PATH",
        [item(text="Unrelated company news.")],
    )
    assert result.assessments[0].label == NewsRelevanceLabel.IRRELEVANT


def test_filter_keeps_direct_and_read_through_only():
    provider = FakeProvider([
        out("READ_THROUGH"),
        out("IRRELEVANT"),
    ])
    items = [
        item("E1", "UiPath announced a product update."),
        item("E2", "A peer announced a sector-wide pricing change."),
        item("E3", "Unrelated company news."),
    ]
    kept, result = path_service(provider).filter_relevant("PATH", items)
    assert [x.evidence.evidence_id for x in kept] == ["E1", "E2"]
    assert result.relevant_evidence_ids == ["E1", "E2"]
    assert len(provider.requests) == 2


def test_original_evidence_is_not_mutated():
    original = item()
    before = original.model_dump(mode="json")
    provider = FakeProvider([])
    path_service(provider).classify("PATH", [original])
    assert original.model_dump(mode="json") == before
    assert provider.requests == []


def test_requests_use_fast_typed_json_relevance_for_non_direct_only():
    provider = FakeProvider([out("IRRELEVANT")])
    path_service(provider).classify(
        "PATH",
        [
            item("E1", "UiPath announced a product update."),
            item("E2", "Workday reported earnings."),
        ],
    )
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.task == AITask.RELEVANCE
    assert request.reasoning_mode == ReasoningMode.FAST
    assert request.response_format == ResponseFormat.JSON
    assert request.output_schema is not None


def test_prompt_is_evidence_only_and_conservative():
    provider = FakeProvider([out("IRRELEVANT")])
    path_service(provider).classify(
        "PATH",
        [item(text="Workday reported earnings.")],
    )
    prompt = provider.requests[0].prompt
    assert "Use only the supplied evidence" in prompt
    assert "If uncertain, choose IRRELEVANT" in prompt
    assert "transmission mechanism" in prompt
    assert "Never return DIRECT" in prompt


def test_ticker_mismatch_is_rejected():
    wrong = item()
    wrong.ticker = "NVDA"
    svc = path_service(FakeProvider([]))
    with pytest.raises(ValueError, match="ticker must match"):
        svc.classify("PATH", [wrong])


def test_prompt_version_is_persisted_in_assessment():
    provider = FakeProvider([])
    result = path_service(provider).classify("PATH", [item()])
    assert result.assessments[0].prompt_version == NEWS_RELEVANCE_PROMPT_VERSION
    assert NEWS_RELEVANCE_PROMPT_VERSION == "news-relevance-v3-canonical-order"


def test_empty_input_returns_empty_result_without_model_call():
    provider = FakeProvider([])
    result = path_service(provider).classify("PATH", [])
    assert result.assessments == []
    assert provider.requests == []


def test_classification_order_is_canonical_by_evidence_id():
    provider = FakeProvider([
        out("IRRELEVANT", rationale="first canonical"),
        out("READ_THROUGH", rationale="second canonical"),
    ])
    result = path_service(provider).classify(
        "PATH",
        [
            item("E2", "Peer two changed sector pricing."),
            item("E1", "Peer one changed sector pricing."),
        ],
    )
    assert [x.evidence_id for x in result.assessments] == ["E1", "E2"]
    assert [r.metadata["evidence_id"] for r in provider.requests] == ["E1", "E2"]


def test_relevance_prompt_version_marks_canonical_order_v3():
    provider = FakeProvider([])
    result = path_service(provider).classify("PATH", [item()])
    assert result.assessments[0].prompt_version == "news-relevance-v3-canonical-order"
