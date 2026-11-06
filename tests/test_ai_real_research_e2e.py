from datetime import datetime, timezone

from app.ai.evidence_aggregator import AggregatedEvidence
from app.ai.evidence_provider import EvidenceKind
from app.ai.models import AIResponse
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    ResearchStatus,
)
from app.ai.research_service import ResearchEvidence, ResearchService
from app.ai.scan_models import (
    CandidateAction,
    CandidateOrigin,
    ScanCandidate,
    SignalType,
)
from app.ai.provider import AIModelProvider

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


class FakeProvider(AIModelProvider):
    def __init__(self):
        self.requests = []

    @property
    def provider_name(self):
        return "FAKE"

    @property
    def model_name(self):
        return "fake-research-model"

    def infer(self, request):
        self.requests.append(request)
        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content="",
            structured_output={
                "research_status": "PARTIAL",
                "market_context": "Strong recent price momentum.",
                "fundamental_context": None,
                "technical_context": "Price is extended versus moving averages.",
                "event_context": "Recent company and sector news is present.",
                "catalyst_assessment": "AI orchestration narrative is a possible catalyst.",
                "expectations_assessment": "UNKNOWN",
                "bull_case": "Continued execution could sustain momentum.",
                "bear_case": "Extended price action raises reversal risk.",
                "key_risks": ["valuation and expectations are not established"],
                "contradictory_evidence": [],
                "unknowns": ["consensus estimates", "valuation"],
                "evidence_quality": "MEDIUM",
                "research_confidence": 0.72,
                "requires_additional_research": True,
            },
            latency_ms=12.0,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )


def candidate():
    return ScanCandidate(
        candidate_id="CAND-PATH-001",
        scan_id="SCAN-PATH-001",
        created_at=NOW,
        ticker="PATH",
        origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG,
        signal_type=SignalType.MULTI_FACTOR,
        raw_score=0.70,
        scanner_confidence=0.70,
        thesis_summary="PATH merits research after strong momentum and recent news.",
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        requires_research=True,
    )


def bundle():
    ev = [
        ResearchEvidence(
            evidence_id="M1",
            source_type="MARKET",
            text="20-session return 42%; RSI14 71; close above SMA20 and SMA50.",
            published_at=NOW,
            metadata={"source_id": "SM1"},
        ),
        ResearchEvidence(
            evidence_id="N1",
            source_type="NEWS",
            text="UiPath highlighted a Maestro deployment at a major customer.",
            published_at=NOW,
            metadata={"source_id": "SN1"},
        ),
    ]
    return AggregatedEvidence(
        ticker="PATH",
        evidence=ev,
        evidence_ids=["M1", "N1"],
        source_ids=["SM1", "SN1"],
        kinds=[EvidenceKind.MARKET, EvidenceKind.NEWS],
        created_at=NOW,
    )


def test_aggregated_bundle_feeds_research_service_end_to_end():
    provider = FakeProvider()
    c = candidate()
    b = bundle()

    result = ResearchService(provider).research(
        c,
        b.evidence,
        portfolio_snapshot_id=c.portfolio_snapshot_id,
        risk_state_id=c.risk_state_id,
        now=NOW,
    )

    assert result.research.candidate_id == c.candidate_id
    assert result.research.scan_id == c.scan_id
    assert result.research.ticker == "PATH"
    assert result.research.evidence_ids == b.evidence_ids
    assert result.research.inference_ids == [result.inference.inference_id]
    assert result.research.research_status == ResearchStatus.PARTIAL
    assert result.research.evidence_quality == EvidenceQuality.MEDIUM
    assert result.research.expectations_assessment == ExpectationsAssessment.UNKNOWN
    assert result.inference.portfolio_snapshot_id == "SNAP-001"
    assert result.inference.risk_state_id == "RISK-001"
    assert len(provider.requests) == 1


def test_research_prompt_contains_every_aggregated_evidence_id():
    provider = FakeProvider()
    c = candidate()
    b = bundle()
    ResearchService(provider).research(c, b.evidence, now=NOW)
    prompt = provider.requests[0].prompt
    assert "M1" in prompt
    assert "N1" in prompt
    assert "20-session return 42%" in prompt
    assert "Maestro deployment" in prompt
