import argparse
from datetime import datetime, timezone

from app.ai.evidence_provider import EvidenceRequest
from app.ai.local_provider import LocalProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="PATH")
    parser.add_argument("--max-items", type=int, default=10)
    args = parser.parse_args()

    news = YahooNewsEvidenceProvider().fetch(
        EvidenceRequest(
            ticker=args.ticker,
            as_of=datetime.now(timezone.utc),
            max_items=args.max_items,
        )
    )

    print("\n=== NEWS RELEVANCE ===")
    print("Ticker:", news.ticker)
    print("Input items:", len(news.items))

    if not news.items:
        print("No news items to classify.")
        return

    service = NewsRelevanceService(
        LocalProvider(model_name="qwen3:8b")
    )
    relevant, result = service.filter_relevant(
        news.ticker, news.items
    )

    for index, (item, assessment) in enumerate(
        zip(news.items, result.assessments), start=1
    ):
        print(f"\n--- {index} ---")
        print("Evidence:", item.evidence.evidence_id)
        print("Label:   ", assessment.label.value)
        print("Conf:    ", f"{assessment.confidence:.2f}")
        print("Why:     ", assessment.rationale)
        print("Text:    ", item.evidence.text)

    print("\nRelevant:", len(relevant))
    print("Discarded:", len(news.items) - len(relevant))


if __name__ == "__main__":
    main()
