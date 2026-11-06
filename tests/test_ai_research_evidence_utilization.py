from datetime import datetime, timezone

from app.ai.models import AIResponse
from app.ai.provider import AIModelProvider
from app.ai.research_service import RESEARCH_PROMPT_VERSION, ResearchEvidence, ResearchService
from app.ai.scan_models import CandidateAction, CandidateOrigin, ScanCandidate, SignalType

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


class FakeProvider(AIModelProvider):
    def __init__(self, output):
        self.output = output
        self.requests = []

    @property
    def provider_name(self): return "FAKE"
    @property
    def model_name(self): return "fake"

    def infer(self, request):
        self.requests.append(request)
        return AIResponse(
            provider="FAKE", model="fake", content="",
            structured_output=self.output, latency_ms=1.0, usage={}
        )


def candidate():
    return ScanCandidate(
        candidate_id="C-PATH", scan_id="S-PATH", created_at=NOW,
        ticker="PATH", origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG, signal_type=SignalType.MULTI_FACTOR,
        raw_score=0.7, scanner_confidence=0.7,
        thesis_summary="Research PATH.", requires_research=True,
    )


def evidence():
    return [
        ResearchEvidence(
            evidence_id="M1", source_type="MARKET", published_at=NOW,
            text="5-session return: 12.31%. 20-session return: 42.61%. SMA20: 15.8. SMA50: 13.2. RSI14: 72.68. RVOL: 0.79x."
        ),
        ResearchEvidence(
            evidence_id="N1", source_type="NEWS", published_at=NOW,
            text="UiPath highlighted a Maestro deployment at Banco Azteca orchestrating more than 8,800 automated processes."
        ),
    ]


def valid_output():
    return {
        "research_status": "PARTIAL",
        "market_context": None,
        "fundamental_context": None,
        "technical_context": "Strong momentum: +12.31% over 5 sessions, +42.61% over 20 sessions; RSI14 72.68 and price above SMA20/SMA50.",
        "event_context": "UiPath highlighted a large Maestro deployment at Banco Azteca.",
        "catalyst_assessment": "Large-scale Maestro adoption may strengthen the AI orchestration narrative.",
        "expectations_assessment": "UNKNOWN",
        "bull_case": "Continued adoption could support momentum.",
        "bear_case": "Extended technical conditions raise reversal risk.",
        "key_risks": ["extended price action"],
        "contradictory_evidence": [],
        "unknowns": ["valuation", "consensus expectations"],
        "evidence_quality": "MEDIUM",
        "research_confidence": 0.65,
        "requires_additional_research": True,
    }


def prompt():
    p = FakeProvider(valid_output())
    ResearchService(p).research(candidate(), evidence(), now=NOW)
    return p.requests[0].prompt


def test_prompt_version_bumped_to_v12():
    assert RESEARCH_PROMPT_VERSION == "opportunity-research-v1.5-context-gaps-confidence"


def test_prompt_requires_technical_context_when_technical_evidence_exists():
    text = prompt()
    assert "technical measurements" in text
    assert "technical_context" in text
    assert "moving averages" in text
    assert "RSI" in text
    assert "RVOL" in text


def test_prompt_requires_event_context_for_supported_news():
    text = prompt()
    assert "identifiable company/event/news developments" in text
    assert "event_context" in text


def test_prompt_prevents_unknowns_from_contradicting_supplied_facts():
    text = prompt()
    assert "Do not list an item as unknown" in text
    assert "Remove unknowns that contradict facts" in text


def test_prompt_preserves_unknown_expectations_when_not_supported():
    text = prompt()
    assert "Strong recent price performance alone does not prove" in text
    result = ResearchService(FakeProvider(valid_output())).research(candidate(), evidence(), now=NOW)
    assert result.research.expectations_assessment.value == "UNKNOWN"


def test_structured_fields_flow_to_opportunity_research():
    r = ResearchService(FakeProvider(valid_output())).research(candidate(), evidence(), now=NOW).research
    assert r.technical_context is not None and "72.68" in r.technical_context
    assert r.event_context is not None and "Maestro" in r.event_context
    assert r.catalyst_assessment is not None and "orchestration" in r.catalyst_assessment
    assert "technical indicators" not in " ".join(r.unknowns).lower()
