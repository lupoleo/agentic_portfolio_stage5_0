from __future__ import annotations

import argparse

from app.ai.opportunity_scoring_service import OpportunityScoringService
from tools import live_opportunity_scoring_8c2 as live


_COMPONENT_NAMES = (
    "thesis",
    "catalyst",
    "fundamental",
    "technical",
    "expectations",
)


def _print_provenance_diagnostic(research, components) -> None:
    canonical = list(research.evidence_ids)
    canonical_set = set(canonical)

    print("\n" + "=" * 72)
    print("AI-8C.2c SCORING EVIDENCE PROVENANCE DIAGNOSTIC")
    print("=" * 72)
    print(f"ticker: {research.ticker}")
    print(f"canonical_research_evidence_count: {len(canonical)}")
    print("canonical_research_evidence_ids:")
    for evidence_id in canonical:
        print(f"  - {evidence_id}")

    all_cited: list[str] = []
    print("\ncomponent citations:")
    for name in _COMPONENT_NAMES:
        assessment = getattr(components, name)
        cited = list(assessment.supporting_evidence_ids)
        all_cited.extend(cited)
        missing = [eid for eid in cited if eid not in canonical_set]

        print(f"  {name.upper()}:")
        print(f"    score: {assessment.score}")
        print(f"    cited_count: {len(cited)}")
        print(f"    cited_ids: {cited if cited else 'NONE'}")
        print(f"    outside_canonical: {missing if missing else 'NONE'}")

    cited_set = set(all_cited)
    outside = sorted(cited_set - canonical_set)
    unused = sorted(canonical_set - cited_set)

    print("\nset comparison:")
    print(f"  all_cited_unique_count: {len(cited_set)}")
    print(f"  cited_minus_canonical: {outside if outside else 'NONE'}")
    print(f"  canonical_minus_cited: {unused if unused else 'NONE'}")
    print("=" * 72 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI-8C.2c scoring evidence provenance diagnostic"
    )
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args()

    original_validate_grounding = OpportunityScoringService._validate_grounding

    def diagnostic_validate_grounding(research, components):
        _print_provenance_diagnostic(research, components)
        # Preserve the existing strict production behavior.
        return original_validate_grounding(research, components)

    OpportunityScoringService._validate_grounding = staticmethod(
        diagnostic_validate_grounding
    )

    # Reuse the already validated live pipeline and its CLI contract.
    import sys
    previous_argv = sys.argv[:]
    try:
        sys.argv = ["live_opportunity_scoring_8c2", "--ticker", args.ticker]
        live.main()
    finally:
        sys.argv = previous_argv
        OpportunityScoringService._validate_grounding = original_validate_grounding


if __name__ == "__main__":
    main()
