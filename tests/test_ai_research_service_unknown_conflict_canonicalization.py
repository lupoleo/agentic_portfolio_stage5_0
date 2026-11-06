from types import SimpleNamespace

from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    ResearchStatus,
)
from app.ai.research_service import (
    ResearchModelOutput,
    ResearchService,
)


def _issue(code):
    return SimpleNamespace(code=SimpleNamespace(value=code))


def _report(*codes):
    errors = [_issue(code) for code in codes]
    return SimpleNamespace(
        errors=errors,
        is_valid=not errors,
    )


def _output(unknowns):
    return ResearchModelOutput(
        research_status=ResearchStatus.INSUFFICIENT_EVIDENCE,
        market_context=None,
        fundamental_context=None,
        technical_context="Latest close 230.36; RSI14 53.68.",
        event_context=None,
        catalyst_assessment=None,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Price is above SMA20 and SMA50.",
        bear_case="Momentum may reverse.",
        key_risks=[],
        contradictory_evidence=[],
        unknowns=list(unknowns),
        evidence_quality=EvidenceQuality.MEDIUM,
        research_confidence=0.70,
        requires_additional_research=True,
    )


class _Validator:
    def validate(self, output, evidence):
        codes = []
        if "bad-unknown" in output.unknowns:
            codes.append("UNKNOWN_CONTRADICTS_SUPPLIED_FACT")
        return _report(*codes)


class _ValidatorWithNewError:
    def validate(self, output, evidence):
        if "bad-unknown" in output.unknowns:
            return _report("UNKNOWN_CONTRADICTS_SUPPLIED_FACT")
        return _report("NEW_UNRELATED_ERROR")


def _service(validator):
    service = object.__new__(ResearchService)
    service.coverage_validator = validator
    return service


def test_post_repair_canonicalization_removes_only_validator_proven_unknown():
    service = _service(_Validator())
    output = _output(["bad-unknown", "legitimate-unknown"])
    initial = service.coverage_validator.validate(output, [])

    repaired, report, changed = service._canonicalize_conflicting_unknowns(
        output,
        [],
        initial,
    )

    assert changed is True
    assert repaired.unknowns == ["legitimate-unknown"]
    assert report.is_valid is True


def test_post_repair_canonicalization_is_noop_without_target_error():
    service = _service(_Validator())
    output = _output(["legitimate-unknown"])
    initial = service.coverage_validator.validate(output, [])

    repaired, report, changed = service._canonicalize_conflicting_unknowns(
        output,
        [],
        initial,
    )

    assert changed is False
    assert repaired.unknowns == ["legitimate-unknown"]
    assert report.is_valid is True


def test_post_repair_canonicalization_does_not_trade_one_error_for_new_error():
    service = _service(_ValidatorWithNewError())
    output = _output(["bad-unknown", "legitimate-unknown"])
    initial = service.coverage_validator.validate(output, [])

    repaired, report, changed = service._canonicalize_conflicting_unknowns(
        output,
        [],
        initial,
    )

    assert changed is False
    assert repaired.unknowns == ["bad-unknown", "legitimate-unknown"]
    assert [issue.code.value for issue in report.errors] == [
        "UNKNOWN_CONTRADICTS_SUPPLIED_FACT"
    ]
