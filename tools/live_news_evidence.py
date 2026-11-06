import argparse
from datetime import datetime, timezone

from app.ai.evidence_provider import EvidenceRequest
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="PATH")
    parser.add_argument("--max-items", type=int, default=10)
    args = parser.parse_args()

    request = EvidenceRequest(
        ticker=args.ticker,
        as_of=datetime.now(timezone.utc),
        max_items=args.max_items,
    )
    result = YahooNewsEvidenceProvider().fetch(request)

    print("\n=== REAL NEWS / EVENT EVIDENCE ===")
    print("Provider:", result.provider)
    print("Ticker:  ", result.ticker)
    print("Status:  ", result.status.value)
    print("Items:   ", len(result.items))

    for i, item in enumerate(result.items, start=1):
        print(f"\n--- Evidence {i} ---")
        print("Evidence ID:", item.evidence.evidence_id)
        print("Published:  ", item.evidence.published_at)
        print("Publisher:  ", item.source.source_name)
        print("URL:        ", item.source.source_url)
        print("Text:       ", item.evidence.text)

    for warning in result.warnings:
        print("Warning:", warning)


if __name__ == "__main__":
    main()
