"""AI-8C.3-R2.2: one direction-free company assessment per listing.

FUNDAMENTAL and EXPECTATIONS describe the company, not the trade. Live
validation showed that asking the scoring model for them inside a directional
prompt, once per hypothesis, produced company-frame values that differed by up
to 22.5 points between the LONG and SHORT sides of the same listing. This
service scores them once, without any direction, from the FUNDAMENTAL and
ANALYST evidence only, and caches the result by the content-addressed evidence
IDs so that both hypotheses of a listing share the same assessment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Iterable

from pydantic import Field

from app.ai.models import AIModel, AIRequest, AITask, ReasoningMode, ResponseFormat
from app.ai.research_service import ResearchEvidence


COMPANY_ASSESSMENT_POLICY = "ai-8c3-company-assessment-v1"
COMPANY_ASSESSMENT_PROMPT_VERSION = "company-assessment-v1-direction-free"
COMPANY_EVIDENCE_SOURCE_TYPES = frozenset({"FUNDAMENTAL", "ANALYST"})
_COMPONENTS = ("fundamental", "expectations")


class _AssessmentTransport(AIModel):
    score: float | None = Field(default=None, ge=0.0, le=100.0)
    rationale: str | None = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class CompanyAssessmentTransport(AIModel):
    fundamental: _AssessmentTransport = Field(default_factory=_AssessmentTransport)
    expectations: _AssessmentTransport = Field(default_factory=_AssessmentTransport)


@dataclass(frozen=True)
class CompanyComponentAssessment:
    score: float | None
    rationale: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompanyAssessment:
    ticker: str
    fingerprint: str
    evidence_ids: tuple[str, ...]
    fundamental: CompanyComponentAssessment
    expectations: CompanyComponentAssessment
    status: str  # ASSESSED | NO_COMPANY_EVIDENCE | FAILED
    policy_version: str = COMPANY_ASSESSMENT_POLICY
    prompt_version: str = COMPANY_ASSESSMENT_PROMPT_VERSION
    diagnostics: tuple[str, ...] = ()

    def component(self, name: str) -> CompanyComponentAssessment:
        return getattr(self, name)

    def to_diagnostics(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "prompt_version": self.prompt_version,
            "status": self.status,
            "fingerprint": self.fingerprint,
            "evidence_ids": list(self.evidence_ids),
            "fundamental": self.fundamental.score,
            "expectations": self.expectations.score,
            "diagnostics": list(self.diagnostics),
        }


def company_evidence(evidence: Iterable[ResearchEvidence]) -> list[ResearchEvidence]:
    """FUNDAMENTAL and ANALYST evidence, ordered by ID for a stable key."""
    selected = [
        item for item in evidence
        if str(item.source_type).upper() in COMPANY_EVIDENCE_SOURCE_TYPES
    ]
    return sorted(selected, key=lambda item: item.evidence_id)


def company_assessment_fingerprint(ticker: str, evidence: list[ResearchEvidence]) -> str:
    payload = {
        "ticker": ticker.strip().upper(),
        "evidence_ids": [item.evidence_id for item in evidence],
        "policy": COMPANY_ASSESSMENT_POLICY,
        "prompt": COMPANY_ASSESSMENT_PROMPT_VERSION,
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _empty() -> CompanyComponentAssessment:
    return CompanyComponentAssessment(score=None, rationale=None, evidence_ids=())


@dataclass
class CompanyAssessmentService:
    """Direction-free company assessment with a per-instance cache."""

    provider: Any
    _cache: dict[str, CompanyAssessment] = field(default_factory=dict)

    def assess(self, ticker: str, evidence: Iterable[ResearchEvidence]) -> CompanyAssessment:
        selected = company_evidence(evidence)
        fingerprint = company_assessment_fingerprint(ticker, selected)
        cached = self._cache.get(fingerprint)
        if cached is not None:
            return cached
        if not selected:
            result = CompanyAssessment(
                ticker=ticker, fingerprint=fingerprint, evidence_ids=(),
                fundamental=_empty(), expectations=_empty(),
                status="NO_COMPANY_EVIDENCE",
            )
            self._cache[fingerprint] = result
            return result
        try:
            result = self._infer(ticker, fingerprint, selected)
        except Exception as exc:  # noqa: BLE001 - fail closed to scoring-model values
            result = CompanyAssessment(
                ticker=ticker, fingerprint=fingerprint,
                evidence_ids=tuple(item.evidence_id for item in selected),
                fundamental=_empty(), expectations=_empty(),
                status="FAILED", diagnostics=(f"{type(exc).__name__}: {exc}"[:300],),
            )
        self._cache[fingerprint] = result
        return result

    def _infer(self, ticker: str, fingerprint: str, selected: list[ResearchEvidence]) -> CompanyAssessment:
        alias_to_id = {f"C{index}": item.evidence_id for index, item in enumerate(selected, start=1)}
        request = AIRequest(
            task=AITask.REASONING,
            prompt=self._prompt(ticker, selected),
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
            output_schema=CompanyAssessmentTransport,
            metadata={
                "prompt_version": COMPANY_ASSESSMENT_PROMPT_VERSION,
                "ticker": ticker,
                "company_assessment_fingerprint": fingerprint,
            },
        )
        response = self.provider.infer(request)
        if response.structured_output is None:
            raise ValueError("company assessment returned no structured output")
        transport = CompanyAssessmentTransport.model_validate(response.structured_output)
        diagnostics: list[str] = []
        components = {}
        for name in _COMPONENTS:
            value = getattr(transport, name)
            cited = tuple(alias_to_id[a] for a in value.supporting_evidence_ids if a in alias_to_id)
            if value.score is None:
                components[name] = _empty()
            elif not cited or not (value.rationale or "").strip():
                # Ungrounded numeric scores are not used (fail closed).
                diagnostics.append(f"{name.upper()}_UNGROUNDED")
                components[name] = _empty()
            else:
                components[name] = CompanyComponentAssessment(
                    score=float(value.score), rationale=value.rationale.strip(), evidence_ids=cited,
                )
        return CompanyAssessment(
            ticker=ticker, fingerprint=fingerprint,
            evidence_ids=tuple(item.evidence_id for item in selected),
            fundamental=components["fundamental"], expectations=components["expectations"],
            status="ASSESSED", diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _prompt(ticker: str, selected: list[ResearchEvidence]) -> str:
        blocks = "\n\n".join(
            f"[C{index}] source_type: {item.source_type}\n{item.text[:2500]}"
            for index, item in enumerate(selected, start=1)
        )
        return f"""You assess a listed company from the company's own point of view.
You are NOT told any trade direction and must not assume one.

COMPANY: {ticker}

SUPPLIED EVIDENCE
{blocks}

Score two dimensions from 0 to 100 using ONLY the supplied evidence, in
increments of 5:
- fundamental: revenue, earnings, margins, cash flow, balance sheet and
  valuation. 0-20 strongly adverse; 25-40 weak; 45-55 mixed or incomplete;
  60-75 clearly supportive; 80+ multiple strong signals.
- expectations: analyst consensus, price targets versus the current price,
  estimates and revisions. 25-40 demanding or adverse; 45-55 broadly priced
  in; 60-75 favorable asymmetry for the company. Return score=null when no
  analyst evidence is supplied.

Every numeric score needs a one-sentence rationale and one or more evidence
aliases (C1, C2, ...). Return ONLY the requested JSON.
"""
