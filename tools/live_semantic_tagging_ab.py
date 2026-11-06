from __future__ import annotations

import argparse
import time
from enum import Enum

from pydantic import Field

from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceRequest
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.models import (
    AIModel,
    AIRequest,
    AITask,
    DataSensitivity,
    ReasoningMode,
    ResponseFormat,
)
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService


ALIASES = {"PATH": ["UiPath"]}
PROMPT_VERSION = "evidence-semantic-tagging-ab-v1"


class SemanticDimension(str, Enum):
    PRICE_TECHNICAL = "PRICE_TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    CATALYST_EVENT = "CATALYST_EVENT"
    ANALYST_EXPECTATIONS = "ANALYST_EXPECTATIONS"
    MACRO = "MACRO"
    NEWS_CONTEXT = "NEWS_CONTEXT"


class SemanticTaggingOutput(AIModel):
    dimensions: list[SemanticDimension] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


def prompt_for(ticker: str, evidence_id: str, text: str) -> str:
    return f"""You classify ONE supplied financial evidence item for ticker {ticker}.
This is multi-label semantic tagging, not investment advice and not a LONG/SHORT decision.

Return every dimension that is EXPLICITLY supported by the supplied text:
- PRICE_TECHNICAL: prices, returns, volume, RSI, moving averages, volatility or technical setup.
- FUNDAMENTAL: revenue, earnings/EPS, margins, cash flow, balance sheet, valuation, growth or profitability.
- CATALYST_EVENT: earnings release, guidance change, product/customer announcement, M&A, regulatory/clinical event, investor day or another identifiable catalyst/event.
- ANALYST_EXPECTATIONS: analyst rating/target, consensus estimate, beat/miss versus expectations, revisions, or explicit market/analyst expectations.
- MACRO: rates, inflation, FX, economic data, central banks, broad macro conditions.
- NEWS_CONTEXT: company/sector news or contextual narrative that is relevant even if another label also applies.

Rules:
1. Multi-label is expected. An earnings article may be FUNDAMENTAL + CATALYST_EVENT + ANALYST_EXPECTATIONS + NEWS_CONTEXT.
2. Do not infer facts that are not in the text.
3. Do not tag FUNDAMENTAL merely because the item discusses a company.
4. Do not tag ANALYST_EXPECTATIONS merely because an article expresses an opinion.
5. Use dimensions=[] if none is explicitly supported.
6. Keep rationale concise and evidence-grounded.

Evidence ID: {evidence_id}
SUPPLIED TEXT:
{text}
"""


def infer(provider, ticker, evidence, mode):
    req = AIRequest(
        task=AITask.RESEARCH,
        prompt=prompt_for(ticker, evidence.evidence_id, evidence.text),
        sensitivity=DataSensitivity.PUBLIC,
        reasoning_mode=mode,
        response_format=ResponseFormat.JSON,
        output_schema=SemanticTaggingOutput,
        metadata={
            "prompt_version": PROMPT_VERSION,
            "benchmark": "AI-7D.4B.0",
            "ticker": ticker,
            "evidence_id": evidence.evidence_id,
            "reasoning_mode": mode.value,
        },
    )
    start = time.perf_counter()
    response = provider.infer(req)
    elapsed = (time.perf_counter() - start) * 1000.0
    if response.structured_output is None:
        raise ValueError("semantic tagging provider returned no structured output")
    return SemanticTaggingOutput.model_validate(response.structured_output), elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--max-news", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()

    ticker = args.ticker.strip().upper()
    market = YahooMarketEvidenceProvider().fetch(EvidenceRequest(ticker=ticker, max_items=20))
    news = YahooNewsEvidenceProvider().fetch(EvidenceRequest(ticker=ticker, max_items=args.max_news))

    provider = LocalProvider(model_name="qwen3:8b", timeout_seconds=args.timeout)
    relevance = NewsRelevanceService(provider, company_aliases=ALIASES).classify(ticker, news.items)
    bundle = EvidenceAggregator().aggregate(ticker, [market, news], news_relevance=relevance)

    # Benchmark semantic tagging only on NEWS evidence. Market evidence is already deterministic.
    news_evidence = [e for e in bundle.evidence if e.source_type == "NEWS_EVENT"]
    if not news_evidence:
        # Be tolerant of provider source naming while remaining explicit in output.
        news_evidence = [e for e in bundle.evidence if "NEWS" in e.source_type.upper()]

    print("\n=== AI-7D.4B.0 SEMANTIC TAGGING A/B ===")
    print("Ticker:             ", ticker)
    print("Relevant news items:", len(news_evidence))
    print("Model:               qwen3:8b")
    print("Prompt:             ", PROMPT_VERSION)

    fast_total = 0.0
    reasoning_total = 0.0
    disagreements = 0

    for idx, evidence in enumerate(news_evidence, 1):
        fast, fast_ms = infer(provider, ticker, evidence, ReasoningMode.FAST)
        reasoning, reasoning_ms = infer(provider, ticker, evidence, ReasoningMode.REASONING)
        fast_total += fast_ms
        reasoning_total += reasoning_ms
        fast_set = {x.value for x in fast.dimensions}
        reasoning_set = {x.value for x in reasoning.dimensions}
        same = fast_set == reasoning_set
        disagreements += 0 if same else 1

        title = evidence.text.replace("\n", " ")[:150]
        print(f"\n[{idx}] {evidence.evidence_id}")
        print("Text:      ", title)
        print("FAST:      ", ", ".join(sorted(fast_set)) or "NONE", f" conf={fast.confidence:.2f}  {fast_ms:.0f}ms")
        print("REASONING: ", ", ".join(sorted(reasoning_set)) or "NONE", f" conf={reasoning.confidence:.2f}  {reasoning_ms:.0f}ms")
        print("Agreement: ", "YES" if same else "NO")
        if not same:
            print("FAST why:  ", fast.rationale)
            print("REAS why:  ", reasoning.rationale)

    n = max(len(news_evidence), 1)
    print("\n--- SUMMARY ---")
    print("Items:                 ", len(news_evidence))
    print("Exact-label agreement: ", f"{len(news_evidence)-disagreements}/{len(news_evidence)}")
    print("Disagreements:          ", disagreements)
    print("FAST avg latency ms:    ", f"{fast_total/n:.0f}")
    print("REASONING avg latency:  ", f"{reasoning_total/n:.0f}")
    if fast_total > 0:
        print("Reasoning/FAST latency: ", f"{reasoning_total/fast_total:.2f}x")
    print("\nNOTE: This benchmark measures semantic tagging behavior only. It does not change production evidence or make a CIO decision.")


if __name__ == "__main__":
    main()
