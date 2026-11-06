from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pydantic import Field
from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceKind, EvidenceRequest
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.models import AIModel, AIRequest, AITask, DataSensitivity, ReasoningMode, ResponseFormat
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService

ALIASES={"PATH":["UiPath"],"SNPS":["Synopsys"]}

class IsolatedFieldRepairOutput(AIModel):
    technical_context: str|None=None
    event_context: str|None=None
    unsupported_fields: list[str]=Field(default_factory=list)

def fmt(i,x):
    e=x.evidence
    return f"[Evidence {i}]\nkind: {x.kind.value}\nsource_type: {e.source_type}\ntext: {e.text}"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--ticker",default="PATH")
    p.add_argument("--max-news",type=int,default=10)
    p.add_argument("--timeout",type=float,default=180.0)
    a=p.parse_args(); ticker=a.ticker.upper(); now=datetime.now(timezone.utc)

    market=YahooMarketEvidenceProvider().fetch(EvidenceRequest(ticker=ticker,as_of=now,max_items=20))
    news=YahooNewsEvidenceProvider().fetch(EvidenceRequest(ticker=ticker,as_of=now,max_items=a.max_news))
    local=LocalProvider(model_name="qwen3:8b",timeout_seconds=a.timeout)
    relevant,relevance=NewsRelevanceService(local,company_aliases=ALIASES).filter_relevant(ticker,news.items)
    bundle=EvidenceAggregator().aggregate(ticker,[market,news],news_relevance=relevance,now=now)
    by_id={x.evidence.evidence_id:x for x in [*market.items,*news.items]}
    selected=[by_id[e.evidence_id] for e in bundle.evidence if e.evidence_id in by_id]
    if len(selected)!=len(bundle.evidence): raise RuntimeError("Canonical evidence reconstruction failed")

    kinds={EvidenceKind.MARKET,EvidenceKind.TECHNICAL,EvidenceKind.NEWS,EvidenceKind.EVENT,
           EvidenceKind.FUNDAMENTAL,EvidenceKind.ANALYST}
    scoped=[x for x in selected if x.kind in kinds]
    block="\n\n".join(fmt(i,x) for i,x in enumerate(scoped,1))
    prompt=f"""Isolated repair diagnostic for financial research. Ticker: {ticker}

Return ONLY technical_context, event_context, unsupported_fields.
Use only SUPPLIED EVIDENCE. Do not invent facts or make a trade decision.
technical_context: summarize supplied price returns, trend, moving averages, RSI, volume/RVOL, volatility or other technical measurements.
event_context: summarize identifiable earnings, guidance, analyst actions, product/customer, regulatory, M&A, macro or company/news developments.
If genuinely unsupported, return null and list the field in unsupported_fields.
Be concise.

SUPPLIED EVIDENCE
{block}"""
    req=AIRequest(task=AITask.EXTRACTION,prompt=prompt,sensitivity=DataSensitivity.PUBLIC,
        reasoning_mode=ReasoningMode.FAST,response_format=ResponseFormat.JSON,
        output_schema=IsolatedFieldRepairOutput,
        metadata={"diagnostic":"AI-7D.4D.2b","ticker":ticker})
    print("=== AI-7D.4D.2b ISOLATED FIELD-REPAIR DIAGNOSTIC ===")
    print("Ticker:",ticker," Selected:",len(selected)," Scoped:",len(scoped)," Relevant news:",len(relevant))
    print("Prompt chars:",len(prompt)," Reasoning: FAST"," Production modified: NO")
    t=time.perf_counter(); r=local.infer(req); elapsed=time.perf_counter()-t
    if r.structured_output is None: raise RuntimeError("No structured output")
    o=IsolatedFieldRepairOutput.model_validate(r.structured_output)
    print("Inference seconds:",round(elapsed,2))
    print(json.dumps(o.model_dump(mode="json"),indent=2,ensure_ascii=False))
    print("technical_context populated:",bool(o.technical_context))
    print("event_context populated:",bool(o.event_context))
    print("unsupported_fields:",o.unsupported_fields)
    print("RESULT:","PASS" if o.technical_context and o.event_context else "FAIL")

if __name__=="__main__": main()
