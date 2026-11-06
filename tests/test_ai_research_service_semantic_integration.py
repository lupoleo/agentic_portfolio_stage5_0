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


class SequenceProvider(AIModelProvider):
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
        output = self.outputs[min(len(self.requests) - 1, len(self.outputs) - 1)]
        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content="{}",
            structured_output={
                key: value
                for key, value in output.model_dump(mode="json").items()
                if key in request.output_schema.model_fields
            },
            latency_ms=1.0,
            usage={},
        )


def candidate():
    return ScanCandidate(
        candidate_id="CAND-PATH-SEM",
        scan_id="SCAN-PATH-SEM",
        created_at=NOW,
        ticker="PATH",
        origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG,
        signal_type=SignalType.FUNDAMENTAL,
        scanner_confidence=0.70,
        thesis_summary="Research PATH.",
    )


def evidence():
    return [
        ResearchEvidence(
            evidence_id="M1",
            source_type="MARKET",
            text="PATH close 18.64; RSI14 74.70; RVOL 0.80x.",
        ),
        ResearchEvidence(
            evidence_id="N1",
            source_type="NEWS",
            text="UiPath announced a Banco Azteca deployment.",
        ),
    ]


def output(status, *, requires_more=True):
    return ResearchModelOutput(
        research_status=status,
        technical_context="PATH close 18.64; RSI14 74.70; RVOL 0.80x.",
        event_context="UiPath announced a Banco Azteca deployment.",
        catalyst_assessment="The deployment could affect investor positioning.",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Momentum and deployment news could support interest.",
        bear_case="Overbought momentum could reverse.",
        key_risks=["Momentum reversal"],
        unknowns=["Financial impact is not quantified."],
        evidence_quality=EvidenceQuality.LOW,
        research_confidence=0.70,
        requires_additional_research=requires_more,
    )


def test_semantic_failure_triggers_same_single_repair_path():
    provider = SequenceProvider([
        output(ResearchStatus.INSUFFICIENT_EVIDENCE),
        output(ResearchStatus.PARTIAL),
    ])
    result = ResearchService(provider).research(candidate(), evidence(), now=NOW)

    assert result.repair_attempted is True
    assert len(provider.requests) == 2
    assert len(result.inferences) == 2
    assert result.research.research_status == ResearchStatus.PARTIAL
    assert result.semantic_report["is_valid"] is True
    assert provider.requests[1].metadata["repair"] is True
    assert provider.requests[1].metadata["semantic_error_codes"] == [
        "USEFUL_ANALYSIS_MARKED_INSUFFICIENT"
    ]


def test_semantic_failure_after_one_repair_is_deterministically_normalized():
    invalid = output(ResearchStatus.INSUFFICIENT_EVIDENCE)
    provider = SequenceProvider([invalid, invalid])

    result = ResearchService(provider).research(candidate(), evidence(), now=NOW)

    assert len(provider.requests) == 2
    assert len(result.inferences) == 2
    assert result.repair_attempted is True
    assert result.research.research_status == ResearchStatus.PARTIAL
    assert result.research.requires_additional_research is True
    assert result.coverage_report["is_valid"] is True
    assert result.semantic_report["is_valid"] is True
    assert result.research.metadata["deterministic_status_normalized"] is True


def test_semantically_valid_initial_output_does_not_repair():
    provider = SequenceProvider([output(ResearchStatus.PARTIAL)])
    result = ResearchService(provider).research(candidate(), evidence(), now=NOW)

    assert result.repair_attempted is False
    assert len(provider.requests) == 1
    assert len(result.inferences) == 1
    assert result.semantic_report["is_valid"] is True
