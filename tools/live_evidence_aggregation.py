import argparse
from datetime import datetime, timezone
from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceRequest
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService
ALIASES={"PATH":["UiPath"]}
def main():
 p=argparse.ArgumentParser(); p.add_argument("--ticker",default="PATH"); p.add_argument("--max-news",type=int,default=10); a=p.parse_args()
 ticker=a.ticker.strip().upper(); now=datetime.now(timezone.utc)
 market=YahooMarketEvidenceProvider().fetch(EvidenceRequest(ticker=ticker,as_of=now,max_items=20))
 news=YahooNewsEvidenceProvider().fetch(EvidenceRequest(ticker=ticker,as_of=now,max_items=a.max_news))
 svc=NewsRelevanceService(LocalProvider(model_name="qwen3:8b"),company_aliases=ALIASES)
 relevant,rel=svc.filter_relevant(ticker,news.items)
 bundle=EvidenceAggregator().aggregate(ticker,[market,news],news_relevance=rel,now=now)
 print("\n=== AI-7D.1 REAL EVIDENCE BUNDLE ===")
 print("Ticker:",ticker); print("Market status:",market.status.value); print("News status:",news.status.value)
 print("Raw news:",len(news.items)); print("Relevant news:",len(relevant)); print("Aggregated evidence:",len(bundle.evidence))
 print("Evidence IDs:",bundle.evidence_ids); print("Kinds:",[x.value for x in bundle.kinds])
 print("Providers:",bundle.metadata["providers"]); print("Warnings:",bundle.warnings)
 print("\nBundle ready for ResearchService / ScanCandidate integration.")
if __name__=="__main__": main()
