from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app.ai.evidence_provider import EvidenceRequest
from app.ai.evidence_quality import EvidenceQualityEvaluator
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService

ALIASES = {
    "PATH": ["UiPath"],
    "SNPS": ["Synopsys"],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI-7D.4A.1 live deterministic evidence quality/coverage smoke test"
    )
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

    local = LocalProvider(model_name="qwen3:8b")
    relevance_service = NewsRelevanceService(
        local,
        company_aliases=ALIASES,
    )
    relevant_news, relevance = relevance_service.filter_relevant(ticker, news.items)

    # Evaluate only evidence that would actually be admitted downstream:
    # all market evidence plus news retained by the relevance gate.
    selected_items = [*market.items, *relevant_news]
    assessment = EvidenceQualityEvaluator().evaluate(selected_items, as_of=now)

    print("\n=== AI-7D.4A.1 LIVE EVIDENCE QUALITY ===")
    print("Ticker:                 ", ticker)
    print("Market evidence:        ", len(market.items))
    print("Raw news:               ", len(news.items))
    print("Relevant news:          ", len(relevant_news))
    print("Selected evidence:      ", assessment.total_items)
    print("Quality:                ", assessment.quality.value)
    print("Coverage score:         ", f"{assessment.coverage_score:.3f}")
    print("Freshness score:        ", f"{assessment.freshness_score:.3f}")
    print("Source diversity score: ", f"{assessment.source_diversity_score:.3f}")
    print("Unique sources:         ", assessment.unique_sources)
    print("Stale items:            ", assessment.stale_items)
    print("Undated items:          ", assessment.undated_items)
    print("Coverage:")
    for entry in assessment.coverage:
        print(
            f"  {entry.dimension.value:<22} "
            f"{entry.level.value:<8} items={entry.item_count}"
        )
    print("Warnings:")
    if assessment.warnings:
        for warning in assessment.warnings:
            print("  -", warning)
    else:
        print("  (none)")
    print("Policy:                 ", assessment.metadata.get("policy"))
    print("\nNOTE: Quality measures observable evidence integrity/freshness/diversity;")
    print("      coverage measures represented research dimensions. No CIO decision is made.")


if __name__ == "__main__":
    main()
