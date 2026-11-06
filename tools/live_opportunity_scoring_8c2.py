from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone

from app.ai.canonical_technical import build_canonical_technical_input
from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceRequest
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService
from app.ai.opportunity_score_models import OpportunityScoringProfile
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_service import ResearchCoverageValidationError, ResearchService
from app.ai.scan_models import ScanCandidate


ALIASES = {"PATH": ["UiPath"], "SNPS": ["Synopsys"]}


def _candidate(ticker: str) -> ScanCandidate:
    fields = ScanCandidate.model_fields

    def first_enum_value(field_name: str, preferred: str | None = None):
        ann = fields[field_name].annotation
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
        "candidate_id": f"LIVE-{ticker}-8C2",
        "scan_id": f"LIVE-SCAN-{ticker}-8C2",
        "created_at": datetime.now(timezone.utc),
        "ticker": ticker,
        "origin": first_enum_value("origin"),
        "action": first_enum_value("action", "LONG"),
        "signal_type": first_enum_value("signal_type"),
        "scanner_confidence": 0.70,
        "thesis_summary": (
            f"Live AI-8C.2 standalone opportunity scoring smoke test for {ticker}; "
            "no portfolio-fit or CIO decision."
        ),
    }
    return ScanCandidate.model_validate(payload)



def _normalize_semantic_text(value):
    if value is None:
        return None
    return " ".join(str(value).split()).strip() or None


def _canonical_research_semantic_input(research) -> dict:
    return {
        "research_status": research.research_status.value,
        "market_context": _normalize_semantic_text(research.market_context),
        "fundamental_context": _normalize_semantic_text(
            research.fundamental_context
        ),
        "technical_context": _normalize_semantic_text(
            research.technical_context
        ),
        "event_context": _normalize_semantic_text(research.event_context),
        "catalyst_assessment": _normalize_semantic_text(
            research.catalyst_assessment
        ),
        "expectations_assessment": research.expectations_assessment.value,
        "bull_case": _normalize_semantic_text(research.bull_case),
        "bear_case": _normalize_semantic_text(research.bear_case),
        "key_risks": [
            _normalize_semantic_text(item)
            for item in research.key_risks
            if _normalize_semantic_text(item) is not None
        ],
        "contradictory_evidence": [
            _normalize_semantic_text(item)
            for item in research.contradictory_evidence
            if _normalize_semantic_text(item) is not None
        ],
        "unknowns": [
            _normalize_semantic_text(item)
            for item in research.unknowns
            if _normalize_semantic_text(item) is not None
        ],
        "evidence_quality": research.evidence_quality.value,
        "research_confidence": research.research_confidence,
        "requires_additional_research": research.requires_additional_research,
    }


def _research_semantic_fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> None:
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

    local = LocalProvider(model_name="qwen3:8b", timeout_seconds=600.0)
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

    if len(items) != len(bundle.evidence):
        missing = [
            evidence.evidence_id
            for evidence in bundle.evidence
            if evidence.evidence_id not in by_id
        ]
        raise RuntimeError(
            "Canonical EvidenceItems missing for selected evidence IDs: "
            f"{missing}"
        )

    research_result = ResearchService(local).research(
        _candidate(ticker),
        bundle.evidence,
        evidence_items=items,
        now=now,
    )

    research = research_result.research
    quality = research_result.evidence_quality_report
    coverage_score = float(quality["coverage_score"])

    canonical_technical = build_canonical_technical_input(ticker)

    scoring_result = OpportunityScoringService(
        local,
    ).score(
        research,
        canonical_technical_input=canonical_technical,
        evidence_coverage_score=coverage_score,
        scoring_profile=OpportunityScoringProfile.STANDARD,
    )

    components = scoring_result.components
    calc = scoring_result.calculation

    print("\n=== RESEARCH PRESENCE / LATENCY PROVENANCE ===")
    research_diag = research_result.diagnostics
    print("Required contexts:          " f"{research_diag.get('required_context_fields', [])}")
    print("Initial missing contexts:   " f"{research_diag.get('initial_missing_required_contexts', [])}")
    print("Final missing contexts:     " f"{research_diag.get('final_missing_required_contexts', [])}")
    print("Presence repair attempted:  " f"{research_diag.get('context_presence_repair_attempted')}")
    print("Semantic tagging wall ms:   " f"{research_diag.get('semantic_tagging_wall_ms')}")
    print("Evidence quality wall ms:   " f"{research_diag.get('evidence_quality_wall_ms')}")
    print("Initial research latency:   " f"{research_diag.get('initial_research_latency_ms')}")
    print("Repair research latency:    " f"{research_diag.get('repair_research_latency_ms')}")
    print("Research provider total ms: " f"{research_diag.get('research_provider_latency_total_ms')}")
    print("Research inference count:   " f"{research_diag.get('research_inference_count')}")
    print("Evidence semantic dimensions:")
    for evidence_id, dimensions in research_diag.get("evidence_semantic_dimensions", {}).items():
        print(f"  {evidence_id}: {dimensions}")
    print("=== END RESEARCH PRESENCE / LATENCY PROVENANCE ===")

    print("\n=== AI-8C.2 LIVE OPPORTUNITY SCORING ===")
    print("Ticker:                    ", ticker)
    print("Evidence:                  ", len(items))
    print("Relevant news:             ", len(relevant))
    print("Research status:           ", research.research_status.value)
    print("Evidence quality:          ", research.evidence_quality.value)
    print("Coverage score:            ", coverage_score)
    print("Research confidence:       ", research.research_confidence)
    print("Expectations research:     ", research.expectations_assessment.value)
    print("Research repair attempted: ", research_result.repair_attempted)

    print("\nAI semantic components:")
    for label, assessment in (
        ("THESIS", components.thesis),
        ("CATALYST", components.catalyst),
        ("FUNDAMENTAL", components.fundamental),
        ("TECHNICAL", components.technical),
        ("EXPECTATIONS", components.expectations),
    ):
        score = "NULL" if assessment.score is None else f"{assessment.score:.2f}"
        ids = ", ".join(assessment.supporting_evidence_ids) or "NONE"
        print(f"  {label:13} score={score}")
        print(f"                evidence={ids}")
        print(f"                rationale={assessment.rationale}")

    print("=== SCORABILITY PROVENANCE ===")
    for component_name, provenance in scoring_result.diagnostics["components"].items():
        print(
            f"  {component_name.upper():12} "
            f"context={provenance['context_present']} "
            f"scorable={provenance['deterministic_scorable']} "
            f"initial={provenance['initial_model_score']} "
            f"scorability_repair={provenance['scorability_repair_attempted']} "
            f"grounding_repair={provenance['grounding_repair_attempted']} "
            f"final={provenance['final_score']}"
        )
    print("=== END SCORABILITY PROVENANCE ===")

    print("=== SCORE-RATIONALE CONSISTENCY ===")
    consistency = scoring_result.diagnostics.get(
        "score_rationale_consistency", {}
    )
    for component_name, item in consistency.get("components", {}).items():
        print(
            f"  {component_name.upper():12} "
            f"status={item['status']} "
            f"supportive={item['supportive_signals']} "
            f"adverse={item['adverse_signals']} "
            f"score={item['score']}"
        )
    print(
        "Possible contradictions:    ",
        consistency.get("possible_contradictions", []),
    )
    print(
        "Contradiction count:        ",
        consistency.get("possible_contradiction_count", 0),
    )
    print("=== END SCORE-RATIONALE CONSISTENCY ===")

    print("\nDeterministic calculation:")
    print("  Raw score:               ", calc.raw_score)
    print("  Component completeness:  ", calc.component_completeness)
    print("  Score confidence:        ", calc.score_confidence)
    print("  Confidence-adjusted:     ", calc.confidence_adjusted_score)
    print("  Normalized weights:      ", calc.normalized_weights)

    print("\nFactors:")
    print("  Positive:                ", components.positive_factors)
    print("  Negative:                ", components.negative_factors)
    print("  Uncertainty:             ", components.uncertainty_factors)

    research_semantic_input = _canonical_research_semantic_input(research)
    variance_payload = {
        "ticker": ticker,
        "research_semantic_fingerprint": _research_semantic_fingerprint(
            research_semantic_input
        ),
        "research_semantic_input": research_semantic_input,
        "selected_evidence_ids": [e.evidence_id for e in bundle.evidence],
        "required_context_fields": research_diag.get("required_context_fields", []),
        "final_missing_required_contexts": research_diag.get(
            "final_missing_required_contexts", []
        ),
        "evidence_semantic_dimensions": research_diag.get(
            "evidence_semantic_dimensions", {}
        ),
        "components": {
            name: {
                "score": getattr(components, name).score,
                "supporting_evidence_ids": list(
                    getattr(components, name).supporting_evidence_ids
                ),
            }
            for name in (
                "thesis",
                "catalyst",
                "fundamental",
                "technical",
                "expectations",
            )
        },
        "scorability": {
            name: {
                "context_present": provenance["context_present"],
                "deterministic_scorable": provenance[
                    "deterministic_scorable"
                ],
                "final_null": provenance["final_null"],
            }
            for name, provenance in scoring_result.diagnostics[
                "components"
            ].items()
        },
        "raw_score": calc.raw_score,
        "confidence_adjusted_score": calc.confidence_adjusted_score,
    }
    import json as _json
    print("\n=== AI-8C.3d.1 VARIANCE JSON ===")
    print(_json.dumps(variance_payload, sort_keys=True))
    print("=== END AI-8C.3d.1 VARIANCE JSON ===")

    print("\nNOTE: Standalone OpportunityScore components only.")
    print("      No Portfolio Filter, promotion, sizing or CIO decision.")


if __name__ == "__main__":
    try:
        main()
    except ResearchCoverageValidationError as exc:
        print("\n=== TARGETED REPAIR DIAGNOSTICS ===")
        initial_codes = [
            issue.code.value for issue in getattr(exc.initial_report, "errors", [])
        ]
        initial_codes += [
            issue.code.value
            for issue in getattr(exc.initial_semantic_report, "errors", [])
        ]
        final_codes = [
            issue.code.value for issue in getattr(exc.final_report, "errors", [])
        ]
        final_codes += [
            issue.code.value
            for issue in getattr(exc.final_semantic_report, "errors", [])
        ]
        print("pre_repair_errors:", initial_codes)
        print("post_repair_errors:", final_codes)
        print(
            "initial_output:",
            exc.initial_output.model_dump(mode="json")
            if exc.initial_output is not None else None,
        )
        print(
            "final_output:",
            exc.final_output.model_dump(mode="json")
            if exc.final_output is not None else None,
        )
        print("inference_ids:", getattr(exc, "inference_ids", None))
        print("=== END REPAIR DIAGNOSTICS ===\n")
        raise
