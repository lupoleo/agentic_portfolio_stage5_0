from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceRequest
from app.ai.evidence_quality_semantic import SemanticEvidenceQualityEvaluator
from app.ai.evidence_semantics import EvidenceSemanticTaggingService
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService


ALIASES = {"PATH": ["UiPath"], "SNPS": ["Synopsys"]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--max-news", type=int, default=10)
    args = p.parse_args()
    ticker = args.ticker.upper()
    now = datetime.now(timezone.utc)

    market = YahooMarketEvidenceProvider().fetch(
        EvidenceRequest(ticker=ticker, as_of=now, max_items=20)
    )
    news = YahooNewsEvidenceProvider().fetch(
        EvidenceRequest(ticker=ticker, as_of=now, max_items=args.max_news)
    )
    local = LocalProvider(model_name="qwen3:8b", timeout_seconds=240.0)
    relsvc = NewsRelevanceService(local, company_aliases=ALIASES)
    relevant, relevance = relsvc.filter_relevant(ticker, news.items)

    bundle = EvidenceAggregator().aggregate(
        ticker, [market, news], news_relevance=relevance, now=now
    )
    by_id = {x.evidence.evidence_id: x for x in [*market.items, *news.items]}
    items = [by_id[x.evidence_id] for x in bundle.evidence if x.evidence_id in by_id]

    semantics = EvidenceSemanticTaggingService(local).classify_many(items)
    result = SemanticEvidenceQualityEvaluator().evaluate(items, semantics, now=now)

    print("\n=== AI-7D.4C SEMANTIC COVERAGE + SOURCE DIVERSITY ===")
    print("Ticker:               ", ticker)
    print("Evidence:             ", len(items))
    print("Relevant news:        ", len(relevant))
    print("Quality:              ", result.quality.value)
    print("Coverage score:       ", f"{result.coverage_score:.3f}")
    print("Freshness score:      ", f"{result.freshness_score:.3f}")
    print("Source diversity:     ", f"{result.source_diversity_score:.3f}")
    print("Independent sources:  ", result.unique_sources)
    print("Stale:                ", result.stale_items)
    print("Undated:              ", result.undated_items)
    print("\nCoverage:")
    for entry in result.coverage:
        print(f"  {entry.dimension.value:22} {entry.level.value:8} items={entry.item_count}")
    print("\nWarnings:")
    if result.warnings:
        for warning in result.warnings:
            print(" ", warning)
    else:
        print("  NONE")
    print("\nSource identities:")
    for source in result.metadata.get("normalized_source_identities", []):
        print(" ", source)
    print("\nPolicy:", result.metadata.get("policy"))
    print("NOTE: No ResearchService integration and no CIO decision.")


if __name__ == "__main__":
    main()
