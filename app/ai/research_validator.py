from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import TYPE_CHECKING, Iterable

from app.ai.research_models import EvidenceQuality, ResearchStatus

if TYPE_CHECKING:
    from app.ai.research_service import ResearchEvidence, ResearchModelOutput


class ResearchCoverageSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ResearchCoverageCode(str, Enum):
    TECHNICAL_CONTEXT_MISSING = "TECHNICAL_CONTEXT_MISSING"
    EVENT_CONTEXT_MISSING = "EVENT_CONTEXT_MISSING"
    COMPLETE_WITH_LOW_EVIDENCE = "COMPLETE_WITH_LOW_EVIDENCE"
    COMPLETE_WITH_MATERIAL_UNKNOWNS = "COMPLETE_WITH_MATERIAL_UNKNOWNS"
    COMPLETE_REQUIRES_MORE = "COMPLETE_REQUIRES_MORE"
    INSUFFICIENT_WITHOUT_MORE_RESEARCH = "INSUFFICIENT_WITHOUT_MORE_RESEARCH"
    UNKNOWN_CONTRADICTS_SUPPLIED_FACT = "UNKNOWN_CONTRADICTS_SUPPLIED_FACT"
    CONTRADICTION_LOOKS_LIKE_RISK = "CONTRADICTION_LOOKS_LIKE_RISK"
    # AI-8C.2-R2: warning that triggers a field-scoped confidence repair.
    RESEARCH_CONFIDENCE_INCOHERENT = "RESEARCH_CONFIDENCE_INCOHERENT"
    # AI-8C.2-R5: warning that triggers a field-scoped reclassification repair.
    FORWARD_ITEMS_IN_UNKNOWNS = "FORWARD_ITEMS_IN_UNKNOWNS"


@dataclass(frozen=True)
class ResearchCoverageIssue:
    code: ResearchCoverageCode
    severity: ResearchCoverageSeverity
    message: str


@dataclass(frozen=True)
class ResearchCoverageReport:
    issues: tuple[ResearchCoverageIssue, ...]

    @property
    def errors(self):
        return tuple(i for i in self.issues if i.severity == ResearchCoverageSeverity.ERROR)

    @property
    def warnings(self):
        return tuple(i for i in self.issues if i.severity == ResearchCoverageSeverity.WARNING)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def repair_instructions(self) -> str:
        if self.is_valid:
            return ""
        lines = [
            "The previous structured research output violates the research coverage contract.",
            "Repair the structured output using ONLY the supplied evidence.",
            "Do not browse, invent, or add facts.",
            "Correct these issues:",
        ]
        for issue in self.errors:
            lines.append(f"- {issue.code.value}: {issue.message}")
        lines += [
            "Return the complete structured object again.",
            "Preserve supported analysis, but move content into the correct structured fields.",
            "Do not make a trade recommendation.",
        ]
        return "\n".join(lines)


class ResearchCoverageValidator:
    _TECHNICAL_TERMS = (
        "rsi", "sma", "moving average", "return", "rvol", "relative volume",
        "volume", "volatility", "close above", "close below", "trend",
    )
    _EVENT_SOURCE_TERMS = (
        "news", "event", "earnings", "guidance", "analyst", "rating",
        "product", "customer", "regulatory", "m&a", "macro",
    )
    _MATERIAL_UNKNOWN_TERMS = (
        "valuation", "revenue", "earnings", "margin", "cash flow",
        "balance sheet", "consensus", "whisper", "expectation",
        "market_context", "market context", "sector", "peer",
        "volatility", "guidance",
    )
    # AI-8C.2-R1: a forward uncertainty that reads as missing as-of data, or a
    # material item with no forward framing, is treated as a material gap.
    _GAP_MARKERS = (
        "not provided", "not supplied", "not disclosed", "not available",
        "unavailable", "missing", "not specified", "not stated",
        "not reported", "not included", "not given", "no data", "lack of",
        "not explicitly",
    )
    _FORWARD_MARKERS = (
        "future", "long-term", "long term", "sustainab", "potential", "will ",
        "could", "may ", "might", "outlook", "trajectory", "going forward",
        "next ", "upcoming", "impact", "effect", "reaction", "if ", "whether",
        "timing", "likelihood", "beyond", "remain", "persist", "durab",
        "success of", "ability to", "execution", "outcome", "revision",
        # AI-8C.2-R5 live examples ("market acceptance of new valuation
        # metrics", "volatility from macroeconomic shifts").
        "acceptance", "macroeconomic", "shifts", "pending",
    )
    # AI-8C.2-R2 context-gap principle: a comparison or finer granularity of a
    # measure the evidence already supplies is recorded but does not block
    # COMPLETE. Each rule needs its underlying anchor in the evidence.
    _COMPARISON_MARKERS = (
        "peer", "relative to", "versus", " vs", "benchmark", "comparison",
        "compared", "comparative", "industry average", "sector average",
        "sector-specific",
    )
    _VALUATION_TERMS = ("valuation", "p/e", "p/b", "multiple", "ev/ebitda", "price-to")
    _VALUATION_ANCHORS = ("p/e valuation multiple", "analyst price target mean")
    _FUNDAMENTAL_ANCHOR = "fundamental company snapshot"
    _GUIDANCE_ANCHORS = (
        "analyst earnings estimate", "analyst revenue estimate", "forward eps estimate",
    )
    _GRANULARITY_MARKERS = ("quarter", "trend", "breakdown", "segment")
    _GRANULAR_MEASURE_ANCHORS = (
        ("margin", ("gross margin:", "operating margin:", "profit margin:")),
        ("revenue", ("revenue:",)),
        ("earnings", ("trailing eps:",)),
        ("eps", ("trailing eps:",)),
        ("cash flow", ("operating cash flow:", "free cash flow:")),
    )
    # Supplied fundamental/analyst facts an unknown must not claim are missing.
    _SUPPLIED_FACT_FAMILIES = (
        (("consensus", "eps estimate", "earnings estimate", "analyst estimate",
          "analyst forecast"), ("analyst earnings estimate", "forward eps estimate")),
        (("price target", "target price"), ("analyst price target mean",)),
        (("p/e", "pe ratio", "price-to-earnings"), ("p/e valuation multiple",)),
        (("operating margin",), ("operating margin:",)),
        (("profit margin", "net margin"), ("profit margin:",)),
        (("operating cash flow",), ("operating cash flow:",)),
        (("free cash flow",), ("free cash flow:",)),
        (("total debt", "debt level", "debt levels"), ("total debt:",)),
    )
    _SUPPLIED_FACT_EXEMPTIONS = (
        "peer", "relative", "sector", "industry", "quarter", "trend", "growth",
        "revision", "guidance", "segment", "breakdown", "p/b", "history",
        "historical", "forward p/e",
    )
    # AI-8C.2-R5 (operator decision 2026-10-05): explicit forward framing in an
    # unknown. Stricter than _FORWARD_MARKERS on purpose, because it removes a
    # blocking gap rather than keeping one.
    _FORWARD_FRAMING = (
        "impact of", "effect of", "future", "sustainability", "sustainable",
        "long-term", "long term", "trajectory", "outlook", "market reaction",
        "market acceptance", "potential", "whether", "timing of", "ability to",
        "success of", "outcome", "will ", "could ", "may ", "might ",
        "risk of", "persistence", "durability", "going forward",
    )
    # As-of wording keeps an item a gap even with forward framing.
    _AS_OF_MARKERS = (
        "latest", "recent", "current", "last quarter", "last year",
        "reported", "historical", "consensus", "guidance",
    )
    _RISK_LIKE_TERMS = (
        "overbought", "oversold", "pullback", "correction", "risk",
        "valuation", "caution", "volatile", "volatility", "reversal",
    )

    def validate(
        self,
        output: "ResearchModelOutput",
        evidence: Iterable["ResearchEvidence"],
    ) -> ResearchCoverageReport:
        evidence_list = list(evidence)
        evidence_blob = self.evidence_blob(evidence_list)
        issues = []

        if self._has_any(evidence_blob, self._TECHNICAL_TERMS) and not self._meaningful(output.technical_context):
            issues.append(ResearchCoverageIssue(
                ResearchCoverageCode.TECHNICAL_CONTEXT_MISSING,
                ResearchCoverageSeverity.ERROR,
                "Material technical evidence is supplied, but technical_context is empty.",
            ))

        if self._has_event_evidence(evidence_list) and not self._meaningful(output.event_context):
            issues.append(ResearchCoverageIssue(
                ResearchCoverageCode.EVENT_CONTEXT_MISSING,
                ResearchCoverageSeverity.ERROR,
                "Material news/event evidence is supplied, but event_context is empty.",
            ))

        if output.research_status == ResearchStatus.COMPLETE and output.evidence_quality == EvidenceQuality.LOW:
            issues.append(ResearchCoverageIssue(
                ResearchCoverageCode.COMPLETE_WITH_LOW_EVIDENCE,
                ResearchCoverageSeverity.ERROR,
                "COMPLETE is incompatible with LOW evidence quality.",
            ))

        if output.research_status == ResearchStatus.COMPLETE and output.requires_additional_research:
            issues.append(ResearchCoverageIssue(
                ResearchCoverageCode.COMPLETE_REQUIRES_MORE,
                ResearchCoverageSeverity.ERROR,
                "COMPLETE cannot require additional research.",
            ))

        if output.research_status == ResearchStatus.INSUFFICIENT_EVIDENCE and not output.requires_additional_research:
            issues.append(ResearchCoverageIssue(
                ResearchCoverageCode.INSUFFICIENT_WITHOUT_MORE_RESEARCH,
                ResearchCoverageSeverity.ERROR,
                "INSUFFICIENT_EVIDENCE must require additional research.",
            ))

        material_unknowns = self.material_gaps(output, evidence_blob)
        if output.research_status == ResearchStatus.COMPLETE and material_unknowns:
            issues.append(ResearchCoverageIssue(
                ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS,
                ResearchCoverageSeverity.ERROR,
                "COMPLETE contains material unknowns: " + "; ".join(material_unknowns[:5]),
            ))

        # AI-8C.2-R5: a PARTIAL research whose only open items are future
        # outcomes (in unknowns or forward_uncertainties) and context gaps is
        # offered a field-scoped reassessment. The model decides.
        forward_framed = self.forward_framed_unknowns(output)
        forward_items = list(getattr(output, "forward_uncertainties", None) or [])
        if (
            output.research_status == ResearchStatus.PARTIAL
            and output.evidence_quality in (EvidenceQuality.MEDIUM, EvidenceQuality.HIGH)
            and (forward_framed or forward_items)
            and not material_unknowns
        ):
            detail = (
                "these unknowns describe future outcomes, not missing as-of data: "
                + "; ".join(forward_framed[:5])
                if forward_framed
                else "every open item is a forward uncertainty or a context gap"
            )
            issues.append(ResearchCoverageIssue(
                ResearchCoverageCode.FORWARD_ITEMS_IN_UNKNOWNS,
                ResearchCoverageSeverity.WARNING,
                "No material as-of fact is missing; " + detail,
            ))

        for value in output.contradictory_evidence or []:
            low = value.lower()
            if self._has_any(low, self._RISK_LIKE_TERMS) and not self._looks_like_tension(low):
                issues.append(ResearchCoverageIssue(
                    ResearchCoverageCode.CONTRADICTION_LOOKS_LIKE_RISK,
                    ResearchCoverageSeverity.WARNING,
                    f"Possible risk placed in contradictory_evidence: {value}",
                ))

        if (
            output.research_status != ResearchStatus.INSUFFICIENT_EVIDENCE
            and output.evidence_quality in (EvidenceQuality.MEDIUM, EvidenceQuality.HIGH)
            and output.research_confidence < self.MIN_COHERENT_CONFIDENCE
        ):
            issues.append(ResearchCoverageIssue(
                ResearchCoverageCode.RESEARCH_CONFIDENCE_INCOHERENT,
                ResearchCoverageSeverity.WARNING,
                (
                    f"research_confidence={output.research_confidence} is incoherent "
                    f"with {output.evidence_quality.value} evidence quality and a "
                    f"{output.research_status.value} assessment."
                ),
            ))

        for unknown in output.unknowns:
            if self._unknown_conflicts_with_evidence(unknown, evidence_blob):
                issues.append(ResearchCoverageIssue(
                    ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT,
                    ResearchCoverageSeverity.ERROR,
                    f"Unknown appears to contradict supplied evidence: {unknown}",
                ))

        return ResearchCoverageReport(tuple(issues))

    MIN_COHERENT_CONFIDENCE = 0.2

    @staticmethod
    def evidence_blob(evidence) -> str:
        return " ".join(f"{x.source_type} {x.text}" for x in evidence).lower()

    def context_gaps(self, items, evidence_blob: str | None) -> list[str]:
        """Items that compare or refine a measure the evidence supplies."""
        if not evidence_blob:
            return []
        gaps = []
        for item in items:
            low = item.lower()
            if self._has_any(low, self._COMPARISON_MARKERS):
                valuation = self._has_any(low, self._VALUATION_TERMS)
                anchors = self._VALUATION_ANCHORS if valuation else (self._FUNDAMENTAL_ANCHOR,)
                if self._has_any(evidence_blob, anchors):
                    gaps.append(item)
                    continue
            if "guidance" in low and self._has_any(evidence_blob, self._GUIDANCE_ANCHORS):
                gaps.append(item)
                continue
            if self._has_any(low, self._GRANULARITY_MARKERS):
                for measure, anchors in self._GRANULAR_MEASURE_ANCHORS:
                    if measure in low and self._has_any(evidence_blob, anchors):
                        gaps.append(item)
                        break
        return gaps

    def forward_framed_unknowns(self, output) -> list[str]:
        """Material unknowns that are explicitly future outcomes."""
        framed = []
        for item in self.material_unknowns(output):
            low = item.lower()
            if (
                self._has_any(low, self._FORWARD_FRAMING)
                and not self._has_any(low, self._GAP_MARKERS)
                and not self._has_any(low, self._AS_OF_MARKERS)
            ):
                framed.append(item)
        return framed

    def material_unknowns(self, output) -> list[str]:
        """Material items among unknowns (missing as-of facts)."""
        return [
            u for u in output.unknowns
            if self._has_any(u.lower(), self._MATERIAL_UNKNOWN_TERMS)
        ]

    def reclassified_forward_uncertainties(self, output) -> list[str]:
        """Forward items that are really material as-of gaps."""
        reclassified = []
        for item in getattr(output, "forward_uncertainties", None) or []:
            low = item.lower()
            gap_marked = self._has_any(low, self._GAP_MARKERS)
            material = self._has_any(low, self._MATERIAL_UNKNOWN_TERMS)
            forward = self._has_any(low, self._FORWARD_MARKERS)
            if gap_marked or (material and not forward):
                reclassified.append(item)
        return reclassified

    def material_gaps(self, output, evidence_blob: str | None = None) -> list[str]:
        """Everything that blocks COMPLETE: material unknowns plus forward
        items that read as missing as-of data, minus context gaps (AI-8C.2-R2)
        when the evidence is known. Without evidence nothing is exempted."""
        candidates = (
            self.material_unknowns(output)
            + self.reclassified_forward_uncertainties(output)
        )
        exempt = set(self.context_gaps(candidates, evidence_blob))
        # AI-8C.2-R5: explicitly forward-framed unknowns are future outcomes
        # filed in the wrong list; they do not block COMPLETE.
        exempt.update(self.forward_framed_unknowns(output))
        return [item for item in candidates if item not in exempt]

    @staticmethod
    def _meaningful(value):
        return bool(value and value.strip())

    @staticmethod
    def _has_any(text, terms):
        return any(term in text for term in terms)

    def _has_event_evidence(self, evidence):
        for item in evidence:
            source = item.source_type.lower()
            text = item.text.lower()
            if self._has_any(source, self._EVENT_SOURCE_TERMS):
                return True
            if self._has_any(text, ("earnings", "guidance", "analyst", "deployment", "customer", "launch", "acquisition", "rating")):
                return True
        return False

    @staticmethod
    def _looks_like_tension(text):
        return any(token in text for token in ("despite", "while", "although", "but", "however", "coexist", "versus", "vs.", "contradict"))

    @staticmethod
    def _unknown_conflicts_with_evidence(unknown, evidence_blob):
        low = unknown.lower()

        # An unknown about the consequence, impact, interpretation or future
        # effect of a supplied fact does not contradict the fact itself.
        implication_qualifiers = (
            "impact",
            "effect",
            "implication",
            "consequence",
            "future",
            "outcome",
            "price performance",
            "price reaction",
            "market reaction",
        )
        if any(term in low for term in implication_qualifiers):
            return False

        # AI-8C.2-R2: fundamental and analyst facts supplied by the evidence.
        if not any(
            term in low
            for term in ResearchCoverageValidator._SUPPLIED_FACT_EXEMPTIONS
            + ResearchCoverageValidator._FORWARD_MARKERS
        ):
            for terms, anchors in ResearchCoverageValidator._SUPPLIED_FACT_FAMILIES:
                if any(term in low for term in terms) and any(
                    anchor in evidence_blob for anchor in anchors
                ):
                    return True

        # AI-8C.2-R1: canonical TECHNICAL evidence states the 20-session
        # annualized volatility (AI-8C.3-R1). An unknown claiming volatility is
        # missing contradicts it, unless it quotes a value (then it is about
        # the future of a known level). "beyond"/"other" do not exempt it.
        if "volatil" in low and "annualized volatility" in evidence_blob:
            return not re.search(r"\d", low)

        families = {
            "rsi": ("rsi",),
            "moving average": ("sma", "moving average"),
            "sma": ("sma", "moving average"),
            "rvol": ("rvol", "relative volume"),
            "relative volume": ("rvol", "relative volume"),
            "return": ("return",),
            "price performance": ("return", "price"),
        }
        for token, evidence_tokens in families.items():
            if token in low and any(x in evidence_blob for x in evidence_tokens):
                if any(q in low for q in ("beyond", "longer-term", "other", "additional")):
                    return False
                return True
        return False
