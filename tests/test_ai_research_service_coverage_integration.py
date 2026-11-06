from datetime import datetime, timezone

import pytest

from app.ai.models import AIResponse
from app.ai.provider import AIModelProvider
from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_service import (
    ResearchCoverageValidationError,
    ResearchEvidence,
    ResearchModelOutput,
    ResearchService,
)
from app.ai.scan_models import CandidateAction, CandidateOrigin, ScanCandidate, SignalType

NOW = datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)


def candidate():
    return ScanCandidate(
        candidate_id="CAND-PATH-INT", scan_id="SCAN-PATH-INT", created_at=NOW,
        ticker="PATH", origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG, signal_type=SignalType.MULTI_FACTOR,
        raw_score=70, scanner_confidence=0.7,
        thesis_summary="Research PATH.", requires_research=True,
    )


def evidence():
    return [
        ResearchEvidence(evidence_id="M1", source_type="MARKET", published_at=NOW,
                         text="20-session return 42%. RSI14 72.5. SMA20 and SMA50 supplied."),
        ResearchEvidence(evidence_id="N1", source_type="NEWS", published_at=NOW,
                         text="UiPath announced a customer deployment."),
    ]


def model(valid=True):
    return ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        market_context=None, fundamental_context=None,
        technical_context="Momentum strong; RSI elevated." if valid else None,
        event_context="Customer deployment announced." if valid else None,
        catalyst_assessment="Adoption may support expectations." if valid else None,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Adoption plus momentum.", bear_case="Extension risk.",
        key_risks=["Extension"], contradictory_evidence=[],
        unknowns=["Valuation"], evidence_quality=EvidenceQuality.MEDIUM,
        research_confidence=0.65, requires_additional_research=True,
    )


class SequenceProvider(AIModelProvider):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []
    @property
    def provider_name(self): return "FAKE"
    @property
    def model_name(self): return "fake-model"
    def infer(self, request):
        self.requests.append(request)
        output = self.outputs[len(self.requests)-1]
        return AIResponse(provider="FAKE", model="fake-model", content="",
                          structured_output={
                key: value
                for key, value in output.model_dump(mode="json").items()
                if key in request.output_schema.model_fields
            },
                          latency_ms=1.0, usage={})


def test_valid_initial_output_does_not_repair():
    provider = SequenceProvider([model(True)])
    result = ResearchService(provider).research(candidate(), evidence(), now=NOW)
    assert len(provider.requests) == 1
    assert result.repair_attempted is False
    assert len(result.inferences) == 1
    assert result.inference.inference_id == result.inferences[0].inference_id
    assert result.research.inference_ids == [result.inference.inference_id]
    assert result.coverage_report["is_valid"] is True


def test_invalid_initial_output_gets_one_repair_and_two_inferences():
    provider = SequenceProvider([model(False), model(True)])
    result = ResearchService(provider).research(candidate(), evidence(), now=NOW)
    assert len(provider.requests) == 2
    assert result.repair_attempted is True
    assert len(result.inferences) == 2
    assert result.inference.inference_id == result.inferences[-1].inference_id
    assert result.research.inference_ids == [x.inference_id for x in result.inferences]
    assert provider.requests[1].metadata["repair"] is True
    assert provider.requests[1].metadata["repair_pass"] == 1
    assert provider.requests[1].metadata["parent_inference_id"] == result.inferences[0].inference_id
    assert result.inferences[0].prompt_version == "opportunity-research-v1.5-context-gaps-confidence"
    assert result.inferences[1].prompt_version == "opportunity-research-v1.5-context-gaps-confidence-repair1"
    assert result.research.technical_context is not None
    assert result.research.event_context is not None


def test_second_invalid_output_fails_closed_after_exactly_two_calls():
    provider = SequenceProvider([model(False), model(False)])
    with pytest.raises(ResearchCoverageValidationError) as caught:
        ResearchService(provider).research(candidate(), evidence(), now=NOW)
    assert len(provider.requests) == 2
    assert len(caught.value.inference_ids) == 2
    assert not caught.value.final_report.is_valid


def test_repair_uses_same_evidence_and_is_constrained():
    provider = SequenceProvider([model(False), model(True)])
    ResearchService(provider).research(candidate(), evidence(), now=NOW)
    prompt = provider.requests[1].prompt
    assert "M1" in prompt and "N1" in prompt
    assert "20-session return 42%" in prompt
    assert "using ONLY the supplied evidence" in prompt
    assert "PREVIOUS STRUCTURED OUTPUT" in prompt


def test_final_research_metadata_records_repair_and_coverage():
    provider = SequenceProvider([model(False), model(True)])
    result = ResearchService(provider).research(candidate(), evidence(), now=NOW)
    assert result.research.metadata["repair_attempted"] is True
    assert result.research.metadata["coverage_valid"] is True
