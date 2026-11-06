from __future__ import annotations

import argparse
import json
from typing import Any

from tools import live_opportunity_scoring_perf_8c2 as perf


FIELDS = (
    "market_context",
    "fundamental_context",
    "technical_context",
    "event_context",
    "catalyst_assessment",
    "bull_case",
    "bear_case",
    "key_risks",
    "contradictory_evidence",
    "unknowns",
)


def _serialized(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _print_research_size_diagnostics(research: Any) -> None:
    print("\n" + "=" * 72)
    print("OPPORTUNITY RESEARCH SIZE DIAGNOSTICS")
    print("=" * 72)

    rows = []
    total = 0
    for field in FIELDS:
        text = _serialized(getattr(research, field, None))
        chars = len(text)
        total += chars
        rows.append((field, chars, round(chars / 4)))

    width = max(len(name) for name, _, _ in rows)
    for name, chars, tokens in rows:
        print(f"{name:<{width}}  chars={chars:>7}  approx_tokens={tokens:>6}")

    print("-" * 72)
    print(f"{'selected_fields_total':<{width}}  chars={total:>7}  approx_tokens={round(total/4):>6}")

    try:
        full_json = research.model_dump_json()
        print(f"{'full_research_json':<{width}}  chars={len(full_json):>7}  approx_tokens={round(len(full_json)/4):>6}")
    except Exception:
        pass

    print("=" * 72 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the existing AI-8C.2 performance diagnostic and print "
                    "per-field OpportunityResearch sizes before scoring."
    )
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args()

    # Reuse the already validated live pipeline, adding only a non-invasive
    # diagnostic hook around OpportunityScoringService.score().
    target_module = perf.live
    original_cls = target_module.OpportunityScoringService

    class DiagnosticOpportunityScoringService(original_cls):
        def score(self, research, **kwargs):
            _print_research_size_diagnostics(research)
            return super().score(research, **kwargs)

    target_module.OpportunityScoringService = DiagnosticOpportunityScoringService
    try:
        # The existing live tool owns the actual pipeline and CLI behavior.
        # Supply its expected argv rather than duplicating any production logic.
        import sys
        old_argv = sys.argv[:]
        sys.argv = [sys.argv[0], "--ticker", args.ticker]
        try:
            target_module.main()
        finally:
            sys.argv = old_argv
    finally:
        target_module.OpportunityScoringService = original_cls


if __name__ == "__main__":
    main()
