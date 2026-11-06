from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceRequest
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService
from app.ai.research_service import ResearchService, ResearchCoverageValidationError
from app.ai.scan_models import ScanCandidate


ALIASES = {"PATH": ["UiPath"], "SNPS": ["Synopsys"]}


def _candidate_from_bundle(ticker: str, bundle) -> ScanCandidate:
    """
    Build the same minimal live research candidate used by the Stage-3 research
    flow, using enum values already defined by ScanCandidate.

    model_validate keeps this tool aligned with the production contract and
    fails closed if the local schema differs.
    """
    fields = ScanCandidate.model_fields

    def first_enum_value(field_name: str, preferred: str | None = None):
        ann = fields[field_name].annotation
        # Pydantic/Enum contract: try preferred first, otherwise first member.
        if preferred is not None:
            try:
                return ann(preferred)
            except Exception:
                pass
        try:
            return list(ann)[0]
        except Exception as exc:
            raise RuntimeError(
                f"Cannot infer live value for ScanCandidate.{field_name}: {ann}"
            ) from exc

    payload = {
        "candidate_id": f"LIVE-{ticker}-7D4D2",
        "scan_id": f"LIVE-SCAN-{ticker}-7D4D2",
        "created_at": datetime.now(timezone.utc),
        "ticker": ticker,
        "origin": first_enum_value("origin"),
        "action": first_enum_value("action", "LONG"),
        "signal_type": first_enum_value("signal_type"),
        "scanner_confidence": 0.70,
        "thesis_summary": (
            f"Live AI-7D.4D.2 integration smoke test for {ticker}; "
            "research only, no trade decision."
        ),
    }
    # Only include required fields. Defaults remain owned by the model.
    required = {
        name for name, field in fields.items()
        if field.is_required()
    }
    payload = {k: v for k, v in payload.items() if k in required or k in fields}
    return ScanCandidate.model_validate(payload)


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

    local = LocalProvider(model_name="qwen3:8b", timeout_seconds=300.0)
    relevance_service = NewsRelevanceService(local, company_aliases=ALIASES)
    relevant, relevance = relevance_service.filter_relevant(ticker, news.items)

    bundle = EvidenceAggregator().aggregate(
        ticker,
        [market, news],
        news_relevance=relevance,
        now=now,
    )

    by_id = {
        item.evidence.evidence_id: item
        for item in [*market.items, *news.items]
    }
    items = [
        by_id[evidence.evidence_id]
        for evidence in bundle.evidence
        if evidence.evidence_id in by_id
    ]

    # Critical integration invariant: ResearchEvidence and canonical EvidenceItem
    # must describe exactly the same selected evidence set.
    if len(items) != len(bundle.evidence):
        missing = [
            evidence.evidence_id
            for evidence in bundle.evidence
            if evidence.evidence_id not in by_id
        ]
        raise RuntimeError(
            "Cannot run ResearchService integration: canonical EvidenceItems "
            f"missing for selected evidence IDs: {missing}"
        )

    candidate = _candidate_from_bundle(ticker, bundle)
    service = ResearchService(local)

    try:
        result = service.research(
            candidate,
            bundle.evidence,
            evidence_items=items,
            now=now,
        )
    except ResearchCoverageValidationError as exc:
        import json
        print("\n=== AI-7D.4D.2a FAIL-CLOSED DIAGNOSTIC ===")
        print("Ticker:", ticker)
        print("Inference IDs:", exc.inference_ids)
        print("\n--- 1. FIRST-PASS OUTPUT ---")
        print(json.dumps(
            exc.initial_output.model_dump(mode="json") if exc.initial_output else None,
            indent=2, ensure_ascii=False))
        print("\n--- 2. INITIAL COVERAGE ---")
        print(json.dumps(service._coverage_report_dict(exc.initial_report),
                         indent=2, ensure_ascii=False))
        print("\n--- 2b. INITIAL SEMANTIC ---")
        print(json.dumps(
            service._semantic_report_dict(exc.initial_semantic_report)
            if exc.initial_semantic_report else None,
            indent=2, ensure_ascii=False))
        print("\n--- 4. FINAL MERGED OUTPUT ---")
        print(json.dumps(
            exc.final_output.model_dump(mode="json") if exc.final_output else None,
            indent=2, ensure_ascii=False))
        print("\n--- 5. FINAL COVERAGE ---")
        print(json.dumps(service._coverage_report_dict(exc.final_report),
                         indent=2, ensure_ascii=False))
        print("\n--- 5b. FINAL SEMANTIC ---")
        print(json.dumps(
            service._semantic_report_dict(exc.final_semantic_report)
            if exc.final_semantic_report else None,
            indent=2, ensure_ascii=False))
        print("\nNOTE: Current production exception exposes first-pass and final merged output,")
        print("but not the raw repair candidate. If needed, the next diagnostic will capture")
        print("raw repair output without changing production semantics.")
        return

    research = result.research
    quality = result.evidence_quality_report

    print("\n=== AI-7D.4D.2 RESEARCHSERVICE LIVE INTEGRATION ===")
    print("Ticker:                    ", ticker)
    print("Evidence:                  ", len(items))
    print("Relevant news:             ", len(relevant))
    print("Research status:           ", research.research_status.value)
    print("Evidence quality:          ", research.evidence_quality.value)
    print("Quality calibrated:        ", research.metadata.get("evidence_quality_calibrated"))
    print("Quality policy:            ", research.metadata.get("evidence_quality_policy"))
    print("Coverage score:            ", quality.get("coverage_score"))
    print("Freshness score:           ", quality.get("freshness_score"))
    print("Source diversity:          ", quality.get("source_diversity_score"))
    print("Independent sources:       ", quality.get("unique_sources"))
    print("Research confidence:       ", research.research_confidence)
    print("Expectations:              ", research.expectations_assessment.value)
    print("Requires more research:    ", research.requires_additional_research)
    print("Repair attempted:          ", result.repair_attempted)
    print("Inference count:           ", len(result.inferences))
    print("Semantic assessments:      ", len(result.evidence_semantic_assessments))
    print("Research coverage valid:   ", result.coverage_report.get("is_valid"))
    print("Research semantic valid:   ", result.semantic_report.get("is_valid"))

    print("\nSemantic evidence coverage:")
    for entry in quality.get("coverage", []):
        print(
            f"  {entry['dimension']:22} "
            f"{entry['level']:8} items={entry['item_count']}"
        )

    print("\nQuality warnings:")
    warnings = quality.get("warnings", [])
    if warnings:
        for warning in warnings:
            print(" ", warning)
    else:
        print("  NONE")

    print("\nResearch contexts:")
    print("  market:     ", research.market_context)
    print("  fundamental:", research.fundamental_context)
    print("  technical:  ", research.technical_context)
    print("  event:      ", research.event_context)
    print("  catalyst:   ", research.catalyst_assessment)

    print("\nUnknowns:")
    if research.unknowns:
        for unknown in research.unknowns:
            print(" ", unknown)
    else:
        print("  NONE")

    print("\nNOTE: ResearchService integration only. No OpportunityScore and no CIO decision.")


if __name__ == "__main__":
    main()
