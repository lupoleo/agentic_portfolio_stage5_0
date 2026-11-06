import argparse
from datetime import datetime, timezone

from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import EvidenceRequest
from app.ai.local_provider import LocalProvider
from app.ai.models import ReasoningMode
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.news_relevance import NewsRelevanceService
from app.ai.research_service import ResearchCoverageValidationError, ResearchService
from app.ai.scan_models import (
    CandidateAction,
    CandidateOrigin,
    ScanCandidate,
    SignalType,
)

ALIASES = {"PATH": ["UiPath"]}


def make_candidate(ticker, now):
    return ScanCandidate(
        candidate_id=f"CAND-{ticker}-LIVE-7D2",
        scan_id=f"SCAN-{ticker}-LIVE-7D2",
        created_at=now,
        ticker=ticker,
        origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG,
        signal_type=SignalType.MULTI_FACTOR,
        raw_score=0.70,
        scanner_confidence=0.70,
        thesis_summary=(
            f"{ticker} selected for real evidence-driven research. "
            "The candidate action is a scan hypothesis, not a CIO decision."
        ),
        portfolio_snapshot_id=None,
        risk_state_id=None,
        requires_research=True,
        metadata={"live_smoke": "AI-7D.2"},
    )



def _print_report(label, report):
    print(f"\n{label}")
    if report is None:
        print("  <not available>")
        return

    errors = getattr(report, "errors", [])
    warnings = getattr(report, "warnings", [])

    print("  Errors:")
    if errors:
        for issue in errors:
            print(f"    - {issue.code.value}: {issue.message}")
    else:
        print("    - none")

    print("  Warnings:")
    if warnings:
        for issue in warnings:
            print(f"    - {issue.code.value}: {issue.message}")
    else:
        print("    - none")


def _print_validation_failure(exc):
    print("\n=== RESEARCH VALIDATION FAILURE ===")
    print(str(exc))
    print("Inference IDs:      ", ", ".join(exc.inference_ids))

    _print_report("INITIAL COVERAGE", exc.initial_report)
    _print_report("INITIAL SEMANTIC", exc.initial_semantic_report)
    _print_report("FINAL COVERAGE", exc.final_report)
    _print_report("FINAL SEMANTIC", exc.final_semantic_report)

    if exc.initial_output is not None:
        print("\nINITIAL STRUCTURED OUTPUT")
        print(exc.initial_output.model_dump_json(indent=2))

    if exc.final_output is not None:
        print("\nREPAIR STRUCTURED OUTPUT")
        print(exc.final_output.model_dump_json(indent=2))

    print(
        "\nIMPORTANT: Research remains fail-closed; "
        "no OpportunityResearch was accepted."
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="PATH")
    parser.add_argument("--max-news", type=int, default=10)
    args = parser.parse_args()

    ticker = args.ticker.strip().upper()
    now = datetime.now(timezone.utc)

    market = YahooMarketEvidenceProvider().fetch(
        EvidenceRequest(ticker=ticker, as_of=now, max_items=20)
    )
    news = YahooNewsEvidenceProvider().fetch(
        EvidenceRequest(ticker=ticker, as_of=now, max_items=args.max_news)
    )

    local = LocalProvider(
        model_name="qwen3:8b",
        timeout_seconds=240.0,
    )
    relevance_service = NewsRelevanceService(
        local,
        company_aliases=ALIASES,
    )
    relevant_news, relevance = relevance_service.filter_relevant(
        ticker, news.items
    )

    bundle = EvidenceAggregator().aggregate(
        ticker,
        [market, news],
        news_relevance=relevance,
        now=now,
    )
    if not bundle.evidence:
        raise RuntimeError("No research evidence available")

    candidate = make_candidate(ticker, now)
    try:
        result = ResearchService(local).research(
            candidate,
            bundle.evidence,
            now=now,
            reasoning_mode=ReasoningMode.FAST,
            )
    except ResearchCoverageValidationError as exc:
        _print_validation_failure(exc)
        return

    r = result.research
    i = result.inference

    print("\n=== AI-7D.2 REAL RESEARCH E2E ===")
    print("Ticker:             ", r.ticker)
    print("Candidate:          ", r.candidate_id)
    print("Evidence count:     ", len(bundle.evidence))
    print("Relevant news:      ", len(relevant_news))
    print("Research status:    ", r.research_status.value)
    print("Evidence quality:   ", r.evidence_quality.value)
    print("Research confidence:", f"{r.research_confidence:.2f}")
    print("Expectations:       ", r.expectations_assessment.value)
    print("Market context:     ", r.market_context)
    print("Fundamental:        ", r.fundamental_context)
    print("Technical:          ", r.technical_context)
    print("Event context:      ", r.event_context)
    print("Catalyst:           ", r.catalyst_assessment)
    print("Bull case:          ", r.bull_case)
    print("Bear case:          ", r.bear_case)
    print("Key risks:")
    for x in r.key_risks:
        print("  -", x)
    print("Contradictory evidence:")
    for x in r.contradictory_evidence:
        print("  -", x)
    print("Unknowns:")
    for x in r.unknowns:
        print("  -", x)
    print("Requires more:      ", r.requires_additional_research)
    print("Inference ID:       ", i.inference_id)
    print("Provider/model:     ", f"{i.provider}/{i.model}")
    print("Prompt version:     ", i.prompt_version)
    print("Latency ms:         ", i.latency_ms)

    print("\nIMPORTANT: This is OpportunityResearch, not a LONG/SHORT CIO decision.")


if __name__ == "__main__":
    main()
