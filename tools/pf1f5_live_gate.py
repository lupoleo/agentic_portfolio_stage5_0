from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

# When this file is executed directly with:
#
#     python tools/pf1f5_live_gate.py
#
# Python places <repo>/tools on sys.path, not the repository root.
# Add the root explicitly before importing app.* modules.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cio.portfolio_lifecycle_gate import (
    PortfolioLifecycleAction,
    PortfolioLifecycleGate,
)
from app.cio.storage import Stage3Store


ADVANCE_DECISIONS = {"PASS", "PASS_WITH_WARNING"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _enum_value(value) -> str:
    return getattr(value, "value", str(value))


def _find_current_advance_assessment(
    db_path: str,
    current_snapshot_id: str,
) -> tuple[str, str] | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT assessment_id, opportunity_id
            FROM portfolio_fit_assessments
            WHERE snapshot_id = ?
              AND decision IN ('PASS', 'PASS_WITH_WARNING')
            ORDER BY created_at DESC, assessment_id DESC
            LIMIT 1
            """,
            (current_snapshot_id,),
        ).fetchone()

    if row is None:
        return None

    return str(row[0]), str(row[1])


def _run_cli_block_path(
    *,
    db_path: str,
    opportunity_id: str,
    assessment_id: str,
) -> str:
    cmd = [
        sys.executable,
        "-m",
        "app.cio.cli",
        "--db",
        db_path,
        "opportunities",
        "instruments",
        opportunity_id,
        "--assessment-id",
        assessment_id,
    ]

    print("\n$", " ".join(cmd))

    completed = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    if completed.stdout:
        print(completed.stdout, end="")

    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")

    _require(
        completed.returncode == 0,
        "Live CLI BLOCK path returned non-zero exit status.",
    )

    return completed.stdout


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "PF-1F.5 live lifecycle-gate acceptance against the canonical "
            "Stage 3 database."
        )
    )
    parser.add_argument(
        "--db",
        default="data/state/portfolio_cio.db",
        help="Canonical Stage3Store SQLite database.",
    )
    parser.add_argument(
        "--reject-opportunity-id",
        default="PF1D4-QCOM-LONG-10000",
        help="Known opportunity whose persisted assessment is REJECT.",
    )
    parser.add_argument(
        "--reject-assessment-id",
        default="PF1F2-QCOM-001",
        help="Known persisted REJECT PortfolioFitAssessment.",
    )
    parser.add_argument(
        "--advance-assessment-id",
        default=None,
        help=(
            "Optional exact persisted PASS/PASS_WITH_WARNING assessment. "
            "If omitted, the harness auto-discovers one on the latest snapshot."
        ),
    )
    parser.add_argument(
        "--require-advance",
        action="store_true",
        help=(
            "Fail if no current-snapshot PASS/PASS_WITH_WARNING assessment "
            "is available. Use this for the final PF-1F.5 freeze gate."
        ),
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path

    _require(db_path.exists(), f"Database not found: {db_path}")

    print("=== PF-1F.5 LIVE GATE + REGRESSION ACCEPTANCE ===")
    print(f"DB: {db_path}")

    store = Stage3Store(str(db_path))
    gate = PortfolioLifecycleGate(store)

    latest_snapshot = store.get_latest_portfolio_snapshot()
    _require(
        latest_snapshot is not None,
        "No latest portfolio snapshot is persisted.",
    )
    current_snapshot_id = latest_snapshot.snapshot_id

    print(f"Current snapshot: {current_snapshot_id}")

    print("\n[A] LIVE REJECT/BLOCK GATE")

    reject_result = gate.evaluate(args.reject_assessment_id)

    print(f"Assessment: {reject_result.assessment_id}")
    print(f"Opportunity: {reject_result.opportunity_id}")
    print(
        "Portfolio decision:",
        _enum_value(reject_result.portfolio_fit_decision),
    )
    print("Lifecycle action:", _enum_value(reject_result.action))
    print("Can advance:", reject_result.can_advance)
    print("Reason:", _enum_value(reject_result.reason_code))

    _require(
        reject_result.opportunity_id == args.reject_opportunity_id,
        "Reject assessment does not belong to the expected opportunity.",
    )
    _require(
        _enum_value(reject_result.portfolio_fit_decision) == "REJECT",
        "Expected persisted negative assessment to be REJECT.",
    )
    _require(
        reject_result.action == PortfolioLifecycleAction.BLOCK,
        "Expected REJECT assessment to map to BLOCK.",
    )
    _require(
        reject_result.can_advance is False,
        "BLOCK result unexpectedly allows lifecycle advancement.",
    )

    cli_out = _run_cli_block_path(
        db_path=str(db_path),
        opportunity_id=args.reject_opportunity_id,
        assessment_id=args.reject_assessment_id,
    )

    _require(
        "Lifecycle action: BLOCK" in cli_out,
        "CLI did not expose BLOCK lifecycle action.",
    )
    _require(
        "Instrument selection blocked." in cli_out,
        "CLI did not fail closed before Instrument Selection.",
    )

    print("[A] PASS")

    print("\n[B] LIVE CURRENT-SNAPSHOT ADVANCE GATE")

    advance_assessment_id = args.advance_assessment_id
    advance_opportunity_id = None

    if advance_assessment_id is None:
        discovered = _find_current_advance_assessment(
            str(db_path),
            current_snapshot_id,
        )
        if discovered is not None:
            advance_assessment_id, advance_opportunity_id = discovered

    if advance_assessment_id is None:
        message = (
            "No persisted PASS/PASS_WITH_WARNING assessment bound to the "
            f"current snapshot {current_snapshot_id} was found."
        )

        if args.require_advance:
            raise RuntimeError(
                message
                + " Create/persist one with `opportunities filter` and rerun "
                  "PF-1F.5."
            )

        print("SKIP:", message)
        print(
            "For the final freeze gate rerun with --require-advance after "
            "persisting a current-snapshot passing assessment."
        )
    else:
        advance_result = gate.evaluate(advance_assessment_id)

        if advance_opportunity_id is None:
            advance_opportunity_id = advance_result.opportunity_id

        decision = _enum_value(
            advance_result.portfolio_fit_decision
        )
        action = _enum_value(advance_result.action)

        print(f"Assessment: {advance_result.assessment_id}")
        print(f"Opportunity: {advance_result.opportunity_id}")
        print(f"Portfolio decision: {decision}")
        print(f"Lifecycle action: {action}")
        print(f"Can advance: {advance_result.can_advance}")
        print(f"Reason: {_enum_value(advance_result.reason_code)}")

        _require(
            decision in ADVANCE_DECISIONS,
            "Positive live assessment is not PASS/PASS_WITH_WARNING.",
        )
        _require(
            action in {"ADVANCE", "ADVANCE_WITH_WARNING"},
            "Passing assessment did not map to an advancing lifecycle action.",
        )
        _require(
            advance_result.can_advance is True,
            "Passing assessment unexpectedly cannot advance.",
        )

        print("[B] PASS")

    print("\n[C] LIVE PROVENANCE REPORT")

    reject_assessment = store.get_portfolio_fit_assessment(
        args.reject_assessment_id
    )
    _require(
        reject_assessment is not None,
        "Reject assessment disappeared from persistence.",
    )

    print(
        "Reject assessment snapshot:",
        reject_assessment.snapshot_id,
    )
    print(
        "Latest portfolio snapshot:",
        current_snapshot_id,
    )

    if reject_assessment.snapshot_id == current_snapshot_id:
        print("Reject assessment is CURRENT.")
    else:
        print("Reject assessment is STALE relative to latest snapshot.")

    print("\n=== PF-1F.5 LIVE GATE PASS ===")
    if advance_assessment_id is None:
        print(
            "Negative live gate passed. Positive live gate is pending because "
            "no current-snapshot passing assessment exists."
        )
    else:
        print(
            "Negative and positive persisted lifecycle-gate paths both passed "
            "against the canonical Stage 3 database."
        )


if __name__ == "__main__":
    main()
