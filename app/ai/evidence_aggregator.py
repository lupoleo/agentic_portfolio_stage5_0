from __future__ import annotations
from datetime import datetime, timezone
from pydantic import Field, model_validator
from app.ai.evidence_provider import EvidenceFetchResult, EvidenceItem, EvidenceKind
from app.ai.models import AIModel
from app.ai.news_relevance import NewsRelevanceLabel, NewsRelevanceResult
from app.ai.research_service import ResearchEvidence

class AggregatedEvidence(AIModel):
    ticker: str
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    kinds: list[EvidenceKind] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bundle(self):
        if self.evidence_ids != [x.evidence_id for x in self.evidence]:
            raise ValueError("evidence_ids must match evidence order exactly")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must be unique")
        return self

class EvidenceAggregator:
    def aggregate(self, ticker: str, fetch_results: list[EvidenceFetchResult],
                  *, news_relevance: NewsRelevanceResult | None = None,
                  now: datetime | None = None) -> AggregatedEvidence:
        ticker=ticker.strip().upper()
        if not ticker: raise ValueError("ticker must be non-empty")
        rel={a.evidence_id:a for a in news_relevance.assessments} if news_relevance else {}
        selected=[]; warnings=[]; providers=[]
        for result in fetch_results:
            if result.ticker != ticker:
                raise ValueError("EvidenceFetchResult ticker must match requested ticker")
            providers.append(result.provider); warnings.extend(result.warnings)
            for item in result.items:
                if item.ticker != ticker:
                    raise ValueError("EvidenceItem ticker must match requested ticker")
                if item.kind == EvidenceKind.NEWS and news_relevance is not None:
                    a=rel.get(item.evidence.evidence_id)
                    if a is None:
                        raise ValueError("Missing news relevance assessment for evidence "+item.evidence.evidence_id)
                    if a.label == NewsRelevanceLabel.IRRELEVANT: continue
                selected.append(item)
        dedup=[]; seen=set(); source_ids=[]; seen_sources=set(); kinds=[]
        for item in selected:
            eid=item.evidence.evidence_id
            if eid in seen: continue
            seen.add(eid); dedup.append(item)

        # Canonical final bundle order prevents provider/feed iteration order
        # from becoming downstream research variance.
        dedup.sort(key=lambda item: (
            item.kind.value,
            item.evidence.evidence_id,
        ))
        for item in dedup:
            if item.source.source_id not in seen_sources:
                seen_sources.add(item.source.source_id); source_ids.append(item.source.source_id)
            if item.kind not in kinds: kinds.append(item.kind)
        ts=now or datetime.now(timezone.utc)
        if ts.tzinfo is None: ts=ts.replace(tzinfo=timezone.utc)
        return AggregatedEvidence(
            ticker=ticker, evidence=[x.evidence for x in dedup],
            evidence_ids=[x.evidence.evidence_id for x in dedup],
            source_ids=source_ids, kinds=kinds,
            warnings=list(dict.fromkeys(warnings)), created_at=ts,
            metadata={"providers":list(dict.fromkeys(providers)),
                      "input_items":sum(len(r.items) for r in fetch_results),
                      "selected_items":len(selected),
                      "deduplicated_items":len(dedup),
                      "news_relevance_applied":news_relevance is not None,
                      "selection_policy_version":"evidence-bundle-v2-canonical-order",
                      "selected_evidence_ids":[x.evidence.evidence_id for x in dedup]})
