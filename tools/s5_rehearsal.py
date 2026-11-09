"""Dry-run REHEARSAL (S5.0-7B) when no opportunity passed the threshold.

    python -m tools.s5_rehearsal [--ticker UCG.MI] [any tools.s5_dry_run option, e.g. --exposure-eur 500]

1. copies data/state/stage5_cio.db and stage5_account_sync.db to
   data/state/rehearsal/ (the real databases are only read);
2. in the copy, promotes the best below-threshold scored hypothesis of the
   last part A run to a TradeOpportunity labelled REHEARSAL (all gates except
   the score threshold still apply);
3. runs ``tools.s5_dry_run`` on the copy, up to a non-authorized ExecutionPlan.

No scan and no Claude call (zero Anthropic credits); IB Gateway is needed for
history, contract details and prices. Nothing is sent to the broker.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from app.e2e.stage5_rehearsal import RehearsalError, copy_state, promote_best_below_threshold
from tools import s5_dry_run
from tools.s5_first_e2e import DEFAULT_OUTPUT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, help="part A report (default: the newest in --output-directory)")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--db", type=Path, default=Path("data/state/stage5_cio.db"))
    parser.add_argument("--sync-db", type=Path, default=Path("data/state/stage5_account_sync.db"))
    parser.add_argument("--rehearsal-directory", type=Path, default=Path("data/state/rehearsal"))
    parser.add_argument("--ticker", help="promote this ticker instead of the best below-threshold score")
    args, dry_run_args = parser.parse_known_args(argv)

    report_path = args.report or s5_dry_run.latest_report(args.output_directory)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    copies = copy_state({"db": args.db, "sync_db": args.sync_db}, args.rehearsal_directory)
    print(f"REHEARSAL on copies in {args.rehearsal_directory} (real state untouched); part A: {report_path.name}")
    output = args.output_directory / "rehearsal"
    try:
        promotion = promote_best_below_threshold(copies["db"], report, now=datetime.now(timezone.utc),
                                                 ticker=args.ticker, output_directory=output)
    except RehearsalError as exc:
        print(f"S5_REHEARSAL: NOT_POSSIBLE ({exc})")
        return 2
    print(f"promoted (REHEARSAL): {promotion.ticker} {promotion.direction} adjusted score "
          f"{promotion.confidence_adjusted_score:.2f} < policy threshold {promotion.policy_threshold:g}")
    return s5_dry_run.main([
        "--report", str(promotion.report_path), "--output-directory", str(output),
        "--db", str(copies["db"]), "--sync-db", str(copies["sync_db"]), *dry_run_args,
    ])


if __name__ == "__main__":
    raise SystemExit(main())
