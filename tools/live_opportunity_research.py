from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app.ai.local_provider import LocalProvider
from app.ai.research_service import ResearchEvidence, ResearchService
from app.ai.scan_models import (
    CandidateAction,
    CandidateOrigin,
    ScanCandidate,
    SignalType,
)


def main():
    parser = argparse.ArgumentParser(
        description="Live local OpportunityResearch smoke test."
    )
    parser.add_argument("--ticker", default="PATH")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    ticker = args.ticker.strip().upper()

    candidate = ScanCandidate(
        candidate_id=f"CAND-LIVE-{ticker}",
        scan_id=f"SCAN-LIVE-{ticker}",
        created_at=now,
        ticker=ticker,
        origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG,
        signal_type=SignalType.FUNDAMENTAL,
        raw_score=70.0,
        scanner_confidence=0.70,
        thesis_summary=(
            "Synthetic candidate used to exercise the research layer."
        ),
        portfolio_snapshot_id=None,
        risk_state_id=None,
        requires_research=True,
    )

    evidence = [
        ResearchEvidence(
            evidence_id="EVID-LIVE-001",
            source_type="SYNTHETIC_TEST",
            published_at=now,
            text=(
                "The company reported improving revenue growth and "
                "management maintained a constructive medium-term outlook. "
                "However, the shares have already rerated materially ahead "
                "of the next catalyst, making expectations potentially "
                "demanding. No current valuation multiple, price, technical "
                "indicator, analyst target, whisper estimate, or exact "
                "consensus figure is supplied."
            ),
        ),
        ResearchEvidence(
            evidence_id="EVID-LIVE-002",
            source_type="SYNTHETIC_TEST",
            published_at=now,
            text=(
                "A bear argument is that stronger expectations may leave "
                "limited tolerance for merely in-line execution. A bull "
                "argument is that sustained growth and stronger guidance "
                "could support further fundamental improvement. The evidence "
                "does not establish whether either scenario is currently "
                "priced correctly by the market."
            ),
        ),
    ]

    service = ResearchService(LocalProvider(model_name="qwen3:8b"))
    result = service.research(candidate, evidence)

    r = result.research

    print()
    print("=== OPPORTUNITY RESEARCH ===")
    print(f"Research ID:       {r.research_id}")
    print(f"Ticker:            {r.ticker}")
    print(f"Status:            {r.research_status.value}")
    print(f"Evidence quality:  {r.evidence_quality.value}")
    print(f"Confidence:        {r.research_confidence:.2f}")
    print(
        "Expectations:      "
        f"{r.expectations_assessment.value}"
    )
    print()
    print(f"Market context:    {r.market_context}")
    print(f"Fundamental:       {r.fundamental_context}")
    print(f"Technical:         {r.technical_context}")
    print(f"Event context:     {r.event_context}")
    print(f"Catalyst:          {r.catalyst_assessment}")
    print()
    print(f"Bull case:         {r.bull_case}")
    print(f"Bear case:         {r.bear_case}")
    print()
    print("Key risks:")
    for item in r.key_risks:
        print(f"  - {item}")
    print("Contradictory evidence:")
    for item in r.contradictory_evidence:
        print(f"  - {item}")
    print("Unknowns:")
    for item in r.unknowns:
        print(f"  - {item}")
    print()
    print(
        "Requires more:     "
        f"{r.requires_additional_research}"
    )
    print(f"Inference ID:      {result.inference.inference_id}")
    print(
        "Provider/model:    "
        f"{result.inference.provider}/"
        f"{result.inference.model}"
    )


if __name__ == "__main__":
    main()
