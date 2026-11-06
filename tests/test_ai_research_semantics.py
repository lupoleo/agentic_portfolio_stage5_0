from datetime import datetime, timezone

from app.ai.models import AIResponse
from app.ai.provider import AIModelProvider
from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_service import (
    RESEARCH_PROMPT_VERSION, ResearchEvidence, ResearchModelOutput, ResearchService,
)
from app.ai.scan_models import CandidateAction, CandidateOrigin, ScanCandidate, SignalType

NOW = datetime(2026, 8, 30, 22, 30, tzinfo=timezone.utc)


class FakeProvider(AIModelProvider):
    @property
    def provider_name(self):
        return "FAKE"

    @property
    def model_name(self):
        return "fake-model"

    def infer(self, ai_request):
        self.last_request = ai_request
        payload = ResearchModelOutput(
            research_status=ResearchStatus.PARTIAL,
            market_context=None,
            fundamental_context="Evidence supports improving revenue growth.",
            technical_context=None,
            event_context="The next catalyst is referenced but not quantified.",
            catalyst_assessment="Sustained growth and stronger guidance are relevant.",
            expectations_assessment=ExpectationsAssessment.UNKNOWN,
            bull_case="Sustained growth and stronger guidance could support further fundamental improvement.",
            bear_case="A prior rerating could leave limited tolerance for merely in-line execution.",
            key_risks=["Demanding expectations after rerating"],
            contradictory_evidence=["Improving fundamentals coexist with elevated expectations."],
            unknowns=["Current valuation", "Consensus and whisper expectations", "Technical indicators"],
            evidence_quality=EvidenceQuality.LOW,
            research_confidence=0.50,
            requires_additional_research=True,
        ).model_dump(mode="json")
        return AIResponse(
            provider=self.provider_name, model=self.model_name, content="",
            structured_output=payload, latency_ms=10.0,
            usage={"prompt_tokens": 100, "completion_tokens": 100},
        )


def candidate():
    return ScanCandidate(
        candidate_id="CAND-001", scan_id="SCAN-001", created_at=NOW,
        ticker="PATH", origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG, signal_type=SignalType.FUNDAMENTAL,
        raw_score=70.0, scanner_confidence=0.70,
        thesis_summary="Potential software opportunity.",
        portfolio_snapshot_id=None, risk_state_id=None, requires_research=True,
    )


def evidence():
    return [ResearchEvidence(
        evidence_id="EVID-001", source_type="SYNTHETIC_TEST",
        text="Revenue growth improved. The shares have rerated materially ahead of the next catalyst.",
        published_at=NOW,
    )]


def get_prompt():
    provider = FakeProvider()
    ResearchService(provider).research(candidate(), evidence(), now=NOW)
    return provider.last_request.prompt


def test_prompt_version_bumped():
    assert RESEARCH_PROMPT_VERSION == "opportunity-research-v1.5-context-gaps-confidence"


def test_prompt_distinguishes_unknown_from_unsupported():
    prompt = get_prompt()
    assert 'UNKNOWN means "not established by supplied evidence"' in prompt
    assert "supported evidence cannot be analysed" in prompt


def test_prompt_requires_supported_analysis_even_with_unknowns():
    prompt = get_prompt()
    assert "Analyse every materially supported claim" in prompt
    assert "Material as-of information missing from the evidence belongs in unknowns" in prompt


def test_prompt_defines_partial_semantics():
    prompt = get_prompt()
    assert "PARTIAL means useful analysis is possible" in prompt
    assert "a material as-of fact" in prompt


def test_partial_low_quality_result_keeps_supported_fields():
    r = ResearchService(FakeProvider()).research(candidate(), evidence(), now=NOW).research
    assert r.research_status == ResearchStatus.PARTIAL
    assert r.evidence_quality == EvidenceQuality.LOW
    assert r.requires_additional_research is True
    assert r.catalyst_assessment is not None
    assert r.bull_case is not None
    assert r.bear_case is not None


def test_expectations_can_remain_unknown_while_analysis_exists():
    r = ResearchService(FakeProvider()).research(candidate(), evidence(), now=NOW).research
    assert r.expectations_assessment == ExpectationsAssessment.UNKNOWN
    assert r.bull_case
    assert r.bear_case
    assert r.unknowns


def test_prompt_forbids_empty_analysis_due_to_uncertainty():
    prompt = get_prompt()
    assert "Analyse every materially supported claim" in prompt
    assert "Do not fill fields merely for completeness" in prompt


def test_inference_uses_new_prompt_version():
    result = ResearchService(FakeProvider()).research(candidate(), evidence(), now=NOW)
    assert result.inference.prompt_version == "opportunity-research-v1.5-context-gaps-confidence"
    assert result.research.metadata["prompt_version"] == "opportunity-research-v1.5-context-gaps-confidence"
