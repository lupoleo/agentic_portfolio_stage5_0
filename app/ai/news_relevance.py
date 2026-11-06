from __future__ import annotations

import re
from enum import Enum

from pydantic import Field

from app.ai.evidence_provider import EvidenceItem
from app.ai.models import (
    AIModel,
    AIRequest,
    AITask,
    DataSensitivity,
    ReasoningMode,
    ResponseFormat,
)
from app.ai.provider import AIModelProvider


NEWS_RELEVANCE_PROMPT_VERSION = "news-relevance-v3-canonical-order"


class NewsRelevanceLabel(str, Enum):
    DIRECT = "DIRECT"
    READ_THROUGH = "READ_THROUGH"
    IRRELEVANT = "IRRELEVANT"


class NewsRelevanceMethod(str, Enum):
    DETERMINISTIC_DIRECT_MATCH = "DETERMINISTIC_DIRECT_MATCH"
    AI_CLASSIFIER = "AI_CLASSIFIER"


class NewsRelevanceModelOutput(AIModel):
    # In V2 Qwen only sees evidence that failed the deterministic direct guard.
    label: NewsRelevanceLabel
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class NewsRelevanceAssessment(AIModel):
    evidence_id: str
    ticker: str
    label: NewsRelevanceLabel
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    method: NewsRelevanceMethod
    provider: str | None = None
    model: str | None = None
    prompt_version: str = NEWS_RELEVANCE_PROMPT_VERSION
    matched_alias: str | None = None


class NewsRelevanceResult(AIModel):
    assessments: list[NewsRelevanceAssessment] = Field(default_factory=list)

    @property
    def relevant_evidence_ids(self) -> list[str]:
        return [
            a.evidence_id
            for a in self.assessments
            if a.label != NewsRelevanceLabel.IRRELEVANT
        ]


class NewsRelevanceService:
    """Hybrid deterministic + local-AI news relevance classifier.

    Explicit target references are classified DIRECT in Python.
    Only evidence without an explicit target reference is sent to the AI,
    which decides READ_THROUGH vs IRRELEVANT.
    """

    def __init__(
        self,
        provider: AIModelProvider,
        *,
        company_aliases: dict[str, list[str]] | None = None,
        prompt_version: str = NEWS_RELEVANCE_PROMPT_VERSION,
    ) -> None:
        self.provider = provider
        self.company_aliases = {
            ticker.strip().upper(): list(aliases)
            for ticker, aliases in (company_aliases or {}).items()
        }
        self.prompt_version = prompt_version

    def classify(
        self,
        ticker: str,
        items: list[EvidenceItem],
        *,
        aliases: list[str] | None = None,
    ) -> NewsRelevanceResult:
        normalized_ticker = ticker.strip().upper()
        if not normalized_ticker:
            raise ValueError("ticker must be non-empty")

        target_aliases = self._target_aliases(
            normalized_ticker,
            aliases=aliases,
        )

        assessments: list[NewsRelevanceAssessment] = []
        canonical_items = sorted(
            items,
            key=lambda x: x.evidence.evidence_id,
        )
        for item in canonical_items:
            if item.ticker != normalized_ticker:
                raise ValueError(
                    "EvidenceItem ticker must match requested ticker"
                )

            matched_alias = self._find_direct_match(
                item.evidence.text,
                target_aliases,
            )
            if matched_alias is not None:
                assessments.append(
                    NewsRelevanceAssessment(
                        evidence_id=item.evidence.evidence_id,
                        ticker=normalized_ticker,
                        label=NewsRelevanceLabel.DIRECT,
                        confidence=1.0,
                        rationale=(
                            "Explicit target reference found in supplied "
                            f"evidence: {matched_alias}"
                        ),
                        method=NewsRelevanceMethod.DETERMINISTIC_DIRECT_MATCH,
                        prompt_version=self.prompt_version,
                        matched_alias=matched_alias,
                    )
                )
                continue

            assessments.append(
                self._classify_read_through(
                    normalized_ticker,
                    item,
                )
            )

        return NewsRelevanceResult(assessments=assessments)

    def filter_relevant(
        self,
        ticker: str,
        items: list[EvidenceItem],
        *,
        aliases: list[str] | None = None,
    ) -> tuple[list[EvidenceItem], NewsRelevanceResult]:
        result = self.classify(ticker, items, aliases=aliases)
        by_id = {
            assessment.evidence_id: assessment
            for assessment in result.assessments
        }
        relevant = [
            item
            for item in items
            if by_id[item.evidence.evidence_id].label
            != NewsRelevanceLabel.IRRELEVANT
        ]
        return relevant, result

    def _target_aliases(
        self,
        ticker: str,
        *,
        aliases: list[str] | None,
    ) -> list[str]:
        raw = [ticker]
        raw.extend(self.company_aliases.get(ticker, []))
        if aliases:
            raw.extend(aliases)

        result: list[str] = []
        seen: set[str] = set()
        for alias in raw:
            value = alias.strip()
            if not value:
                continue
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @staticmethod
    def _find_direct_match(
        text: str,
        aliases: list[str],
    ) -> str | None:
        for alias in aliases:
            # Alphanumeric boundaries prevent ticker PATH matching words
            # such as "pathway", while still matching "(PATH)" / "NYSE:PATH".
            pattern = rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"
            if re.search(pattern, text, flags=re.IGNORECASE):
                return alias
        return None

    def _classify_read_through(
        self,
        ticker: str,
        item: EvidenceItem,
    ) -> NewsRelevanceAssessment:
        request = AIRequest(
            task=AITask.RELEVANCE,
            prompt=self._build_prompt(ticker, item),
            sensitivity=DataSensitivity.PUBLIC,
            reasoning_mode=ReasoningMode.FAST,
            response_format=ResponseFormat.JSON,
            output_schema=NewsRelevanceModelOutput,
            metadata={
                "prompt_version": self.prompt_version,
                "ticker": ticker,
                "evidence_id": item.evidence.evidence_id,
                "direct_match": False,
            },
        )
        response = self.provider.infer(request)
        if response.structured_output is None:
            raise ValueError(
                "News relevance provider returned no structured output"
            )

        output = NewsRelevanceModelOutput.model_validate(
            response.structured_output
        )

        # DIRECT is impossible after the deterministic guard. If the model
        # nevertheless emits it, fail closed rather than silently changing
        # the meaning of the classification.
        if output.label == NewsRelevanceLabel.DIRECT:
            raise ValueError(
                "AI classifier returned DIRECT after deterministic "
                "direct-match guard"
            )

        return NewsRelevanceAssessment(
            evidence_id=item.evidence.evidence_id,
            ticker=ticker,
            label=output.label,
            confidence=output.confidence,
            rationale=output.rationale.strip(),
            method=NewsRelevanceMethod.AI_CLASSIFIER,
            provider=response.provider,
            model=response.model,
            prompt_version=self.prompt_version,
        )

    def _build_prompt(
        self,
        ticker: str,
        item: EvidenceItem,
    ) -> str:
        return f"""You are a financial-news read-through classifier.

Target ticker: {ticker}

Evidence:
{item.evidence.text}

A deterministic system has already established that the supplied evidence
does NOT explicitly reference the target ticker or any configured company
alias.

Choose exactly one relevance label:

READ_THROUGH:
- The evidence contains a concrete peer, sector, customer, supplier,
  macroeconomic, regulatory, or market development with a plausible
  transmission mechanism to the target.
- The mechanism must be supported by the supplied evidence itself.
- Mere similarity such as "both are AI companies" or "both are software
  companies" is NOT enough.

IRRELEVANT:
- No concrete transmission mechanism to the target is established by the
  supplied evidence.
- If you need outside knowledge to create the relationship, choose
  IRRELEVANT.
- If uncertain, choose IRRELEVANT.

Rules:
1. Use only the supplied evidence. Do not browse and do not add facts.
2. Never return DIRECT. Explicit direct references were handled upstream.
3. This is relevance classification, not sentiment or trade advice.
4. rationale must state the evidence-grounded transmission mechanism for
   READ_THROUGH, or why none exists for IRRELEVANT.
5. confidence is confidence in this relevance classification.
6. Return only the requested structured output.
"""
