from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceRequest
from app.ai.evidence_semantics import (
    EvidenceSemanticTaggingService,
    dimension_counts,
)
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService


ALIASES = {"PATH": ["UiPath"], "SNPS": ["Synopsys"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--max-news", type=int, default=10)
    args = parser.parse_args()

    ticker = args.ticker.strip().upper()
    now = datetime.now(timezone.utc)

    market = YahooMarketEvidenceProvider().fetch(
        EvidenceRequest(ticker=ticker, as_of=now, max_items=20)
    )
    news = YahooNewsEvidenceProvider().fetch(
        EvidenceRequest(ticker=ticker, as_of=now, max_items=args.max_news)
    )

    local = LocalProvider(model_name="qwen3:8b", timeout_seconds=240.0)
    relevance_service = NewsRelevanceService(local, company_aliases=ALIASES)
    relevant_news, relevance = relevance_service.filter_relevant(
        ticker, news.items
    )

    bundle = EvidenceAggregator().aggregate(
        ticker,
        [market, news],
        news_relevance=relevance,
        now=now,
    )

    # Map selected ResearchEvidence back to canonical EvidenceItem so source
    # kind remains available to the semantic tagger.
    by_id = {
        x.evidence.evidence_id: x
        for x in [*market.items, *news.items]
    }
    selected_items = [
        by_id[x.evidence_id]
        for x in bundle.evidence
        if x.evidence_id in by_id
    ]

    service = EvidenceSemanticTaggingService(local)
    assessments = service.classify_many(selected_items)
    counts = dimension_counts(assessments)

    print("\n=== AI-7D.4B LIVE SEMANTIC DIMENSIONS ===")
    print("Ticker:             ", ticker)
    print("Selected evidence:  ", len(selected_items))
    print("Relevant news:      ", len(relevant_news))
    print()

    for assessment in assessments:
        labels = ", ".join(x.value for x in assessment.dimensions) or "NONE"
        deterministic = (
            ", ".join(x.value for x in assessment.deterministic_dimensions)
            or "NONE"
        )
        ai = ", ".join(x.value for x in assessment.ai_dimensions) or "NONE"
        print(assessment.evidence_id)
        print("  dimensions:   ", labels)
        print("  deterministic:", deterministic)
        print("  ai-fast:      ", ai)
        print("  method:       ", assessment.method.value)
        if assessment.warnings:
            print("  warnings:     ", "; ".join(assessment.warnings))

    print("\n--- DIMENSION COUNTS ---")
    for dimension, count in counts.items():
        print(f"{dimension.value:22} {count}")

    print(
        "\nNOTE: EvidenceKind is preserved. Semantic dimensions are multi-label "
        "annotations only; no CIO decision is made."
    )


if __name__ == "__main__":
    main()
