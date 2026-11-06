import argparse
from datetime import datetime, timezone

from app.ai.evidence_provider import EvidenceRequest
from app.ai.local_provider import LocalProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService


DEFAULT_ALIASES = {
    "PATH": ["UiPath"],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="PATH")
    parser.add_argument("--max-items", type=int, default=10)
    args = parser.parse_args()

    ticker = args.ticker.strip().upper()
    news = YahooNewsEvidenceProvider().fetch(
        EvidenceRequest(
            ticker=ticker,
            as_of=datetime.now(timezone.utc),
            max_items=args.max_items,
        )
    )

    print("\n=== NEWS RELEVANCE V2 ===")
    print("Ticker:", news.ticker)
    print("Input items:", len(news.items))

    if not news.items:
        print("No news items to classify.")
        return

    service = NewsRelevanceService(
        LocalProvider(model_name="qwen3:8b"),
        company_aliases=DEFAULT_ALIASES,
    )
    relevant, result = service.filter_relevant(ticker, news.items)

    ai_calls = 0
    direct_matches = 0
    for index, (item, assessment) in enumerate(
        zip(news.items, result.assessments), start=1
    ):
        if assessment.method.value == "AI_CLASSIFIER":
            ai_calls += 1
        else:
            direct_matches += 1

        print(f"\n--- {index} ---")
        print("Evidence:", item.evidence.evidence_id)
        print("Label:   ", assessment.label.value)
        print("Method:  ", assessment.method.value)
        print("Conf:    ", f"{assessment.confidence:.2f}")
        print("Alias:   ", assessment.matched_alias)
        print("Why:     ", assessment.rationale)
        print("Text:    ", item.evidence.text)

    print("\nRelevant:", len(relevant))
    print("Discarded:", len(news.items) - len(relevant))
    print("Deterministic direct matches:", direct_matches)
    print("Qwen calls:", ai_calls)


if __name__ == "__main__":
    main()
