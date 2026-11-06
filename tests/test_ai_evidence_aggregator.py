from datetime import datetime, timezone
import pytest
from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceFetchResult,EvidenceFetchStatus,EvidenceItem,EvidenceKind,EvidenceSource
from app.ai.news_relevance import NewsRelevanceAssessment,NewsRelevanceLabel,NewsRelevanceMethod,NewsRelevanceResult
from app.ai.research_service import ResearchEvidence
NOW=datetime(2026,8,31,tzinfo=timezone.utc)
def ei(eid,sid,kind,text,ticker="PATH"):
 return EvidenceItem(evidence=ResearchEvidence(evidence_id=eid,source_type=kind.value,text=text,published_at=NOW,metadata={"source_id":sid}),source=EvidenceSource(source_id=sid,provider="TEST",source_type=kind.value,source_name="Test",retrieved_at=NOW,published_at=NOW),kind=kind,ticker=ticker)
def fr(provider,items,ticker="PATH",warnings=None):
 return EvidenceFetchResult(provider=provider,ticker=ticker,status=EvidenceFetchStatus.SUCCESS,items=items,warnings=warnings or [],fetched_at=NOW)
def ar(eid,label):
 return NewsRelevanceAssessment(evidence_id=eid,ticker="PATH",label=label,confidence=1,rationale="test",method=NewsRelevanceMethod.DETERMINISTIC_DIRECT_MATCH if label==NewsRelevanceLabel.DIRECT else NewsRelevanceMethod.AI_CLASSIFIER)
def test_combines_and_filters():
 m=ei("M1","SM",EvidenceKind.MARKET,"market"); n1=ei("N1","S1",EvidenceKind.NEWS,"direct"); n2=ei("N2","S2",EvidenceKind.NEWS,"bad")
 rel=NewsRelevanceResult(assessments=[ar("N1",NewsRelevanceLabel.DIRECT),ar("N2",NewsRelevanceLabel.IRRELEVANT)])
 r=EvidenceAggregator().aggregate("PATH",[fr("M",[m]),fr("N",[n1,n2])],news_relevance=rel,now=NOW)
 assert r.evidence_ids==["M1","N1"] and r.source_ids==["SM","S1"]
def test_irrelevant_excluded():
 n=ei("N","S",EvidenceKind.NEWS,"x"); rel=NewsRelevanceResult(assessments=[ar("N",NewsRelevanceLabel.IRRELEVANT)])
 assert EvidenceAggregator().aggregate("PATH",[fr("N",[n])],news_relevance=rel,now=NOW).evidence==[]
def test_raw_news_retained_without_filter():
 n=ei("N","S",EvidenceKind.NEWS,"x"); assert EvidenceAggregator().aggregate("PATH",[fr("N",[n])],now=NOW).evidence_ids==["N"]
def test_missing_assessment_fails():
 n=ei("N","S",EvidenceKind.NEWS,"x")
 with pytest.raises(ValueError,match="Missing news relevance"): EvidenceAggregator().aggregate("PATH",[fr("N",[n])],news_relevance=NewsRelevanceResult(),now=NOW)
def test_dedup_evidence_preserves_first():
 a=ei("E","S1",EvidenceKind.MARKET,"first"); b=ei("E","S2",EvidenceKind.MARKET,"second")
 r=EvidenceAggregator().aggregate("PATH",[fr("A",[a]),fr("B",[b])],now=NOW); assert len(r.evidence)==1 and r.evidence[0].text=="first"
def test_source_ids_dedup():
 a=ei("E1","S",EvidenceKind.MARKET,"a"); b=ei("E2","S",EvidenceKind.TECHNICAL,"b")
 assert EvidenceAggregator().aggregate("PATH",[fr("A",[a,b])],now=NOW).source_ids==["S"]
def test_result_ticker_mismatch():
 with pytest.raises(ValueError,match="EvidenceFetchResult ticker"): EvidenceAggregator().aggregate("PATH",[fr("A",[],ticker="NVDA")],now=NOW)
def test_item_ticker_mismatch():
 b=ei("E","S",EvidenceKind.MARKET,"x",ticker="NVDA")
 with pytest.raises(ValueError,match="EvidenceItem ticker"): EvidenceAggregator().aggregate("PATH",[fr("A",[b])],now=NOW)
def test_warnings_providers_dedup():
 r=EvidenceAggregator().aggregate("PATH",[fr("A",[],warnings=["late"]),fr("A",[],warnings=["late","partial"])],now=NOW)
 assert r.warnings==["late","partial"] and r.metadata["providers"]==["A"]
def test_empty_valid():
 r=EvidenceAggregator().aggregate("PATH",[],now=NOW); assert r.evidence==[] and r.metadata["input_items"]==0


def test_bundle_order_is_canonical_independent_of_fetch_order():
 a=ei("Z","SZ",EvidenceKind.MARKET,"z"); b=ei("A","SA",EvidenceKind.MARKET,"a")
 x=EvidenceAggregator().aggregate("PATH",[fr("P",[a,b])],now=NOW)
 y=EvidenceAggregator().aggregate("PATH",[fr("P",[b,a])],now=NOW)
 assert x.evidence_ids==y.evidence_ids==["A","Z"]


def test_bundle_selection_policy_provenance():
 a=ei("A","SA",EvidenceKind.MARKET,"a")
 r=EvidenceAggregator().aggregate("PATH",[fr("P",[a])],now=NOW)
 assert r.metadata["selection_policy_version"]=="evidence-bundle-v2-canonical-order"
 assert r.metadata["selected_evidence_ids"]==["A"]
