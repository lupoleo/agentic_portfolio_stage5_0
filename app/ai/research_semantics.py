from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable, Protocol

from app.ai.research_models import EvidenceQuality, ResearchStatus


class ResearchSemanticCode(str, Enum):
    USEFUL_ANALYSIS_MARKED_INSUFFICIENT = "USEFUL_ANALYSIS_MARKED_INSUFFICIENT"
    PARTIAL_WITHOUT_MORE_RESEARCH = "PARTIAL_WITHOUT_MORE_RESEARCH"
    COMPLETE_WITH_MORE_RESEARCH = "COMPLETE_WITH_MORE_RESEARCH"
    LOW_QUALITY_COMPLETE = "LOW_QUALITY_COMPLETE"
    CONTRADICTION_LOOKS_LIKE_RISK = "CONTRADICTION_LOOKS_LIKE_RISK"
    UNSUPPORTED_GENERALIZATION_RISK = "UNSUPPORTED_GENERALIZATION_RISK"


class ResearchSemanticSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ResearchOutputLike(Protocol):
    research_status: ResearchStatus
    market_context: str | None
    fundamental_context: str | None
    technical_context: str | None
    event_context: str | None
    catalyst_assessment: str | None
    bull_case: str | None
    bear_case: str | None
    key_risks: list[str]
    contradictory_evidence: list[str]
    unknowns: list[str]
    evidence_quality: EvidenceQuality
    research_confidence: float
    requires_additional_research: bool


class ResearchEvidenceLike(Protocol):
    evidence_id: str
    source_type: str
    text: str


@dataclass(frozen=True)
class ResearchSemanticIssue:
    code: ResearchSemanticCode
    severity: ResearchSemanticSeverity
    message: str


@dataclass(frozen=True)
class ResearchSemanticReport:
    issues: tuple[ResearchSemanticIssue, ...]
    useful_analysis: bool
    supported_dimensions: tuple[str, ...]

    @property
    def errors(self) -> tuple[ResearchSemanticIssue, ...]:
        return tuple(x for x in self.issues if x.severity == ResearchSemanticSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ResearchSemanticIssue, ...]:
        return tuple(x for x in self.issues if x.severity == ResearchSemanticSeverity.WARNING)

    @property
    def is_valid(self) -> bool:
        return not self.errors


class ResearchSemanticQualityEvaluator:
    """
    Deterministic second-line semantic checks.

    This evaluator does NOT decide a trade and does NOT rewrite research.
    It checks whether status/quality semantics are coherent with the amount
    of useful analysis actually produced. It is deliberately separate from
    ResearchCoverageValidator: coverage asks whether required evidence was
    used; this module asks whether the research labels describe that output
    coherently.

    The first version is intentionally conservative:
    - status coherence can be an ERROR;
    - contradiction/generalization checks are WARNING-only because lexical
      heuristics cannot prove hallucination or contradiction.
    """

    _TECH = re.compile(
        r"\b(rsi|sma\d*|moving average|rvol|relative volume|return|momentum|"
        r"volatility|trend|close|price)\b", re.I
    )
    _EVENT = re.compile(
        r"\b(earnings|guidance|analyst|upgrade|downgrade|hold|deployment|"
        r"launch|product|customer|regulatory|fda|trial|merger|acquisition|"
        r"investor day|book|contract)\b", re.I
    )
    _FUND = re.compile(
        r"\b(revenue|earnings|margin|cash flow|balance sheet|valuation|"
        r"ebitda|eps|free cash flow)\b", re.I
    )
    _MARKET = re.compile(
        r"\b(sector|peer|industry|market|index|rally|selloff|positioning)\b", re.I
    )
    _TENSION = re.compile(
        r"\b(while|whereas|despite|although|but|however|coexist|conflict|"
        r"contradict|tension|offset)\b", re.I
    )
    _RISK_ONLY = re.compile(
        r"\b(overbought|oversold|correction|valuation risk|downside risk|"
        r"uncertainty|may limit|could fall|could decline)\b", re.I
    )
    _BROAD_GENERALIZATION = re.compile(
        r"\b(broad|industry-wide|sector-wide|companies are|market is|"
        r"investors are|institutions are|widely|generally)\b", re.I
    )

    def evaluate(
        self,
        output: ResearchOutputLike,
        evidence: Iterable[ResearchEvidenceLike],
    ) -> ResearchSemanticReport:
        evidence_list = list(evidence)
        dimensions = self._supported_dimensions(output)
        useful = self._has_useful_analysis(output, dimensions)
        issues: list[ResearchSemanticIssue] = []

        status = ResearchStatus(output.research_status)
        quality = EvidenceQuality(output.evidence_quality)

        if status == ResearchStatus.INSUFFICIENT_EVIDENCE and useful:
            issues.append(ResearchSemanticIssue(
                ResearchSemanticCode.USEFUL_ANALYSIS_MARKED_INSUFFICIENT,
                ResearchSemanticSeverity.ERROR,
                "Research is labelled INSUFFICIENT_EVIDENCE although it contains "
                "a usable multi-sided assessment with supported context. Prefer "
                "PARTIAL when useful analysis exists but material evidence is missing.",
            ))

        if status == ResearchStatus.PARTIAL and not output.requires_additional_research:
            issues.append(ResearchSemanticIssue(
                ResearchSemanticCode.PARTIAL_WITHOUT_MORE_RESEARCH,
                ResearchSemanticSeverity.ERROR,
                "PARTIAL research must require additional research.",
            ))

        if status == ResearchStatus.COMPLETE and output.requires_additional_research:
            issues.append(ResearchSemanticIssue(
                ResearchSemanticCode.COMPLETE_WITH_MORE_RESEARCH,
                ResearchSemanticSeverity.ERROR,
                "COMPLETE research cannot require additional research.",
            ))

        if status == ResearchStatus.COMPLETE and quality == EvidenceQuality.LOW:
            issues.append(ResearchSemanticIssue(
                ResearchSemanticCode.LOW_QUALITY_COMPLETE,
                ResearchSemanticSeverity.ERROR,
                "LOW evidence quality is not coherent with COMPLETE research.",
            ))

        for item in output.contradictory_evidence:
            if self._RISK_ONLY.search(item) and not self._TENSION.search(item):
                issues.append(ResearchSemanticIssue(
                    ResearchSemanticCode.CONTRADICTION_LOOKS_LIKE_RISK,
                    ResearchSemanticSeverity.WARNING,
                    f"Contradictory evidence may be a standalone risk rather than "
                    f"a genuine tension: {item}",
                ))

        evidence_text = " ".join(x.text for x in evidence_list).lower()
        for field_name in ("market_context", "event_context"):
            text = getattr(output, field_name)
            if (
                text
                and self._BROAD_GENERALIZATION.search(text)
                and not self._broad_claim_anchored(text, evidence_text)
            ):
                issues.append(ResearchSemanticIssue(
                    ResearchSemanticCode.UNSUPPORTED_GENERALIZATION_RISK,
                    ResearchSemanticSeverity.WARNING,
                    f"{field_name} contains a broad claim that is not obviously "
                    "anchored in supplied evidence; review provenance.",
                ))

        return ResearchSemanticReport(
            issues=tuple(issues),
            useful_analysis=useful,
            supported_dimensions=tuple(dimensions),
        )

    @staticmethod
    def _supported_dimensions(output: ResearchOutputLike) -> list[str]:
        fields = (
            ("market", output.market_context),
            ("fundamental", output.fundamental_context),
            ("technical", output.technical_context),
            ("event", output.event_context),
            ("catalyst", output.catalyst_assessment),
        )
        return [name for name, value in fields if value and value.strip()]

    @staticmethod
    def _has_useful_analysis(output: ResearchOutputLike, dimensions: list[str]) -> bool:
        # "Useful" is intentionally a high bar. A single populated context field
        # is not enough to override INSUFFICIENT_EVIDENCE.
        has_two_sided_thesis = bool(
            output.bull_case and output.bull_case.strip()
            and output.bear_case and output.bear_case.strip()
        )
        has_context = len(dimensions) >= 2
        has_risk_or_unknown = bool(
            output.key_risks
            or output.unknowns
            or getattr(output, "forward_uncertainties", None)
        )
        return has_two_sided_thesis and has_context and has_risk_or_unknown

    @classmethod
    def _broad_claim_anchored(cls, claim: str, evidence_text: str) -> bool:
        # Warning-only heuristic. Require at least one meaningful phrase/token
        # from the broad claim to be visibly present in the evidence corpus.
        tokens = [
            t.lower()
            for t in re.findall(r"[A-Za-z][A-Za-z-]{4,}", claim)
            if t.lower() not in {
                "broad", "market", "sector", "companies", "investors",
                "generally", "strong", "positive", "negative", "current",
            }
        ]
        if not tokens:
            return False
        matches = sum(1 for token in set(tokens) if token in evidence_text)
        return matches >= min(2, len(set(tokens)))
