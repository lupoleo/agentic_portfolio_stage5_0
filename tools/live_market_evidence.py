from app.ai.evidence_provider import EvidenceRequest
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
import argparse

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--ticker",default="PATH")
    args=ap.parse_args()
    result=YahooMarketEvidenceProvider().fetch(EvidenceRequest(ticker=args.ticker))
    print("\n=== REAL MARKET EVIDENCE ===")
    print("Provider:",result.provider)
    print("Ticker:  ",result.ticker)
    print("Status:  ",result.status.value)
    for item in result.items:
        print("Evidence ID:",item.evidence.evidence_id)
        print("Published:  ",item.evidence.published_at)
        print("Source:     ",item.source.source_name)
        print("URL:        ",item.source.source_url)
        print("Facts:      ",item.evidence.text)
    for warning in result.warnings: print("Warning:",warning)

if __name__=="__main__": main()
