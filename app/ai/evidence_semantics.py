from __future__ import annotations

import re
from enum import Enum

from pydantic import Field

from app.ai.evidence_provider import EvidenceItem, EvidenceKind
from app.ai.models import (
    AIModel,
    AIRequest,
    AITask,
    DataSensitivity,
    ReasoningMode,
    ResponseFormat,
)
from app.ai.provider import AIModelProvider


SEMANTIC_TAGGING_PROMPT_VERSION = "evidence-semantic-tagging-v2-reproducible"


class EvidenceSemanticDimension(str, Enum):
    PRICE_TECHNICAL = "PRICE_TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    CATALYST_EVENT = "CATALYST_EVENT"
    ANALYST_EXPECTATIONS = "ANALYST_EXPECTATIONS"
    MACRO = "MACRO"
    NEWS_CONTEXT = "NEWS_CONTEXT"


class SemanticTaggingMethod(str, Enum):
    DETERMINISTIC_GUARD = "DETERMINISTIC_GUARD"
    AI_FAST = "AI_FAST"
    HYBRID = "HYBRID"


class SemanticTaggingModelOutput(AIModel):
    dimensions: list[EvidenceSemanticDimension] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class EvidenceSemanticAssessment(AIModel):
    evidence_id: str
    ticker: str
    dimensions: list[EvidenceSemanticDimension]
    deterministic_dimensions: list[EvidenceSemanticDimension] = Field(
        default_factory=list
    )
    ai_dimensions: list[EvidenceSemanticDimension] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    method: SemanticTaggingMethod
    rationale: str
    prompt_version: str = SEMANTIC_TAGGING_PROMPT_VERSION
    warnings: list[str] = Field(default_factory=list)


# These guards intentionally detect only explicit financial semantics.
# They are not intended to perform financial interpretation.
_FUNDAMENTAL = re.compile(
    r"\b("
    r"revenue|sales|earnings|eps|earnings per share|net income|"
    r"operating income|operating margin|gross margin|free cash flow|"
    r"cash flow|ebitda|valuation|multiple|p\/e|price[- ]to[- ]earnings|"
    r"profitability|profit|loss|guidance|outlook"
    r")\b",
    re.I,
)
_ANALYST = re.compile(
    r"\b("
    r"analyst|analysts|price target|price targets|consensus|estimate|"
    r"estimates|brokerage recommendation|rating|ratings|upgrade|downgrade|"
    r"hold rating|buy rating|sell rating"
    r")\b",
    re.I,
)
_EVENT = re.compile(
    r"\b("
    r"earnings call|earnings report|quarterly earnings|reports? earnings|"
    r"guidance|outlook|raises? (?:full[- ]year )?guidance|"
    r"lowers? (?:full[- ]year )?guidance|"
    r"acquisition|acquires?|merger|deployment|deploys?|customer adoption|"
    r"product launch|launches?|partnership|contract|investor day|"
    r"regulatory approval|fda|clinical trial|restructuring|royalty"
    r")\b",
    re.I,
)
_NEWS_CONTEXT = re.compile(
    r"\b("
    r"strategy|strategic|positioning|repositioning|narrative|competition|"
    r"competitive|market share|industry|sector|peer|peers|business model|"
    r"management commentary|executive commentary|company commentary"
    r")\b",
    re.I,
)

_PRICE = re.compile(
    r"(?:\b(?:shares?|stock|price)\b.{0,35}\b(?:rose|fell|rall(?:y|ied)|"
    r"jumped|dropped|gained|lost|up|down)\b)|"
    r"(?:[+-]?\d+(?:\.\d+)?%\b)|"
    r"\b(?:rsi|sma\d*|moving average|rvol|relative volume|volatility)\b",
    re.I,
)
_MACRO = re.compile(
    r"\b("
    r"inflation|cpi|pce|interest rates?|fed(?:eral reserve)?|ecb|"
    r"monetary policy|fiscal policy|gdp|unemployment|employment report|"
    r"nonfarm payrolls?|treasury yields?|bond yields?|dxy|dollar index|"
    r"exchange rate|fx|recession|ism\b"
    r")",
    re.I,
)


def deterministic_semantic_dimensions(text: str) -> list[EvidenceSemanticDimension]:
    found: list[EvidenceSemanticDimension] = []
    checks = (
        (_PRICE, EvidenceSemanticDimension.PRICE_TECHNICAL),
        (_FUNDAMENTAL, EvidenceSemanticDimension.FUNDAMENTAL),
        (_EVENT, EvidenceSemanticDimension.CATALYST_EVENT),
        (_ANALYST, EvidenceSemanticDimension.ANALYST_EXPECTATIONS),
        (_MACRO, EvidenceSemanticDimension.MACRO),
        (_NEWS_CONTEXT, EvidenceSemanticDimension.NEWS_CONTEXT),
    )
    for pattern, dimension in checks:
        if pattern.search(text):
            found.append(dimension)
    return found


class EvidenceSemanticTaggingService:
    """
    Multi-label semantic tagging for evidence.

    Governance:
    - explicit financial semantics are captured by deterministic guards;
    - Qwen/other provider is used in FAST mode for the semantic long tail;
    - deterministic tags cannot be removed by AI;
    - every AI-added canonical dimension requires an explicit textual anchor;
    - source kind is preserved; semantic dimensions do not replace EvidenceKind.
    """

    def __init__(
        self,
        provider: AIModelProvider,
        prompt_version: str = SEMANTIC_TAGGING_PROMPT_VERSION,
    ) -> None:
        self.provider = provider
        self.prompt_version = prompt_version

    def classify(self, item: EvidenceItem) -> EvidenceSemanticAssessment:
        text = item.evidence.text
        deterministic = deterministic_semantic_dimensions(text)

        # Deterministic market evidence is already canonical quantitative data.
        if item.kind == EvidenceKind.MARKET:
            dimensions = self._ordered_unique(
                [EvidenceSemanticDimension.PRICE_TECHNICAL, *deterministic]
            )
            return EvidenceSemanticAssessment(
                evidence_id=item.evidence.evidence_id,
                ticker=item.ticker,
                dimensions=dimensions,
                deterministic_dimensions=dimensions,
                ai_dimensions=[],
                confidence=1.0,
                method=SemanticTaggingMethod.DETERMINISTIC_GUARD,
                rationale="Canonical market evidence is PRICE_TECHNICAL.",
                prompt_version=self.prompt_version,
            )

        request = AIRequest(
            task=AITask.RELEVANCE,
            prompt=self._build_prompt(item),
            sensitivity=DataSensitivity.PUBLIC,
            reasoning_mode=ReasoningMode.FAST,
            response_format=ResponseFormat.JSON,
            output_schema=SemanticTaggingModelOutput,
            metadata={
                "prompt_version": self.prompt_version,
                "ticker": item.ticker,
                "evidence_id": item.evidence.evidence_id,
                "semantic_tagging": True,
            },
        )
        response = self.provider.infer(request)
        if response.structured_output is None:
            raise ValueError("semantic tagging provider returned no structured output")
        output = SemanticTaggingModelOutput.model_validate(
            response.structured_output
        )

        ai = list(output.dimensions)
        warnings: list[str] = []

        # AI-8C.3b.7: AI_FAST may discover candidate dimensions, but a
        # stochastic candidate can enter the canonical taxonomy only when the
        # supplied evidence contains a deterministic textual anchor. This makes
        # repeated classification of identical evidence reproducible while
        # preserving AI_FAST as a diagnostic long-tail classifier.
        ai = self._canonicalize_ai_dimensions(text, ai, warnings)

        dimensions = self._ordered_unique([*deterministic, *ai])
        method = (
            SemanticTaggingMethod.HYBRID
            if deterministic and ai
            else (
                SemanticTaggingMethod.DETERMINISTIC_GUARD
                if deterministic and not ai
                else SemanticTaggingMethod.AI_FAST
            )
        )

        return EvidenceSemanticAssessment(
            evidence_id=item.evidence.evidence_id,
            ticker=item.ticker,
            dimensions=dimensions,
            deterministic_dimensions=deterministic,
            ai_dimensions=ai,
            confidence=output.confidence,
            method=method,
            rationale=output.rationale,
            prompt_version=self.prompt_version,
            warnings=warnings,
        )

    def classify_many(
        self,
        items: list[EvidenceItem],
    ) -> list[EvidenceSemanticAssessment]:
        return [self.classify(item) for item in items]

    @staticmethod
    def _canonicalize_ai_dimensions(
        text: str,
        ai_dimensions: list[EvidenceSemanticDimension],
        warnings: list[str],
    ) -> list[EvidenceSemanticDimension]:
        anchors = {
            EvidenceSemanticDimension.PRICE_TECHNICAL: _PRICE,
            EvidenceSemanticDimension.FUNDAMENTAL: _FUNDAMENTAL,
            EvidenceSemanticDimension.CATALYST_EVENT: _EVENT,
            EvidenceSemanticDimension.ANALYST_EXPECTATIONS: _ANALYST,
            EvidenceSemanticDimension.MACRO: _MACRO,
            EvidenceSemanticDimension.NEWS_CONTEXT: _NEWS_CONTEXT,
        }
        accepted: list[EvidenceSemanticDimension] = []
        warning_names = {
            EvidenceSemanticDimension.PRICE_TECHNICAL:
                "AI_PRICE_TECHNICAL_REMOVED_WITHOUT_EXPLICIT_ANCHOR",
            EvidenceSemanticDimension.FUNDAMENTAL:
                "AI_FUNDAMENTAL_REMOVED_WITHOUT_EXPLICIT_ANCHOR",
            EvidenceSemanticDimension.CATALYST_EVENT:
                "AI_CATALYST_EVENT_REMOVED_WITHOUT_EXPLICIT_ANCHOR",
            EvidenceSemanticDimension.ANALYST_EXPECTATIONS:
                "AI_ANALYST_EXPECTATIONS_REMOVED_WITHOUT_EXPLICIT_ANCHOR",
            EvidenceSemanticDimension.MACRO:
                "AI_MACRO_REMOVED_WITHOUT_EXPLICIT_MACRO_ANCHOR",
            EvidenceSemanticDimension.NEWS_CONTEXT:
                "AI_NEWS_CONTEXT_REMOVED_WITHOUT_EXPLICIT_ANCHOR",
        }
        for dimension in ai_dimensions:
            pattern = anchors[dimension]
            if pattern.search(text):
                accepted.append(dimension)
            else:
                warnings.append(warning_names[dimension])
        return EvidenceSemanticTaggingService._ordered_unique(accepted)

    @staticmethod
    def _ordered_unique(
        values: list[EvidenceSemanticDimension],
    ) -> list[EvidenceSemanticDimension]:
        out: list[EvidenceSemanticDimension] = []
        seen: set[EvidenceSemanticDimension] = set()
        for value in values:
            if value not in seen:
                seen.add(value)
                out.append(value)
        return out

    def _build_prompt(self, item: EvidenceItem) -> str:
        return f"""Classify the supplied financial evidence into zero or more
semantic dimensions. This is taxonomy, not investment reasoning.

Ticker: {item.ticker}
Evidence:
{item.evidence.text}

Allowed dimensions:
- PRICE_TECHNICAL: explicit price moves, returns, trend, moving averages,
  RSI, volume/RVOL or volatility.
- FUNDAMENTAL: revenue, earnings/EPS, margins, cash flow, balance sheet,
  valuation, profitability, guidance/outlook financial content.
- CATALYST_EVENT: an identifiable company/market event such as earnings,
  guidance change, acquisition, product/customer deployment, partnership,
  regulatory/clinical event, contract, investor day or business-model change.
- ANALYST_EXPECTATIONS: analyst ratings, price targets, consensus,
  estimates/revisions or brokerage recommendations.
- MACRO: rates, inflation, employment, GDP, FX, monetary/fiscal policy or
  broad economic releases. Institutional buying, sector rallies, peer
  performance and generic market sentiment are NOT MACRO.
- NEWS_CONTEXT: relevant narrative/context not better represented solely by
  the dimensions above.

Rules:
1. Multi-label classification is allowed.
2. Tag only dimensions explicitly supported by the supplied text.
3. Do not infer missing facts.
4. A comparison article or 'better investment' framing is not by itself a
   CATALYST_EVENT.
5. A price move is PRICE_TECHNICAL, not MACRO.
6. Institutional accumulation is not MACRO.
7. Return only the requested structured output.
"""


def dimension_counts(
    assessments: list[EvidenceSemanticAssessment],
) -> dict[EvidenceSemanticDimension, int]:
    counts = {dimension: 0 for dimension in EvidenceSemanticDimension}
    for assessment in assessments:
        for dimension in assessment.dimensions:
            counts[dimension] += 1
    return counts
