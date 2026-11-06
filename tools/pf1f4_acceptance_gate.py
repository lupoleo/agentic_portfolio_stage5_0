from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    print("\n$", " ".join(cmd))
    completed = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
    )

    if completed.stdout:
        print(completed.stdout, end="")

    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    return completed


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "PF-1F.4 acceptance gate: CLI contract + exact persisted "
            "assessment BLOCK path."
        )
    )
    parser.add_argument(
        "--db",
        default="data/state/portfolio_cio.db",
        help="Stage3Store SQLite database.",
    )
    parser.add_argument(
        "--opportunity-id",
        default="PF1D4-QCOM-LONG-10000",
        help="Opportunity used for the negative acceptance path.",
    )
    parser.add_argument(
        "--assessment-id",
        default="PF1F2-QCOM-001",
        help="Persisted REJECT assessment used for the negative acceptance path.",
    )
    args = parser.parse_args()

    cli_path = Path("app/cio/cli.py")
    require(
        cli_path.exists(),
        "Run from repository root; app/cio/cli.py was not found.",
    )

    print("=== PF-1F.4 CLI / SYNTHETIC E2E ACCEPTANCE ===")

    help_run = run(
        [
            sys.executable,
            "-m",
            "app.cio.cli",
            "opportunities",
            "instruments",
            "--help",
        ]
    )
    require(help_run.returncode == 0, "instruments --help failed")
    require(
        "--assessment-id ASSESSMENT_ID" in help_run.stdout,
        "Instrument Selection does not expose explicit --assessment-id.",
    )

    tests_run = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_pf1f4_cli_synthetic_e2e.py",
            "tests/test_pf1f3_lifecycle_integration.py",
            "tests/test_portfolio_lifecycle_gate.py",
            "tests/test_portfolio_filter_cli.py",
            "-q",
        ]
    )
    require(tests_run.returncode == 0, "PF-1F focused acceptance failed")

    block_run = run(
        [
            sys.executable,
            "-m",
            "app.cio.cli",
            "--db",
            args.db,
            "opportunities",
            "instruments",
            args.opportunity_id,
            "--assessment-id",
            args.assessment_id,
        ]
    )
    require(block_run.returncode == 0, "Live BLOCK CLI command failed")

    out = block_run.stdout
    require(
        "Portfolio decision: REJECT" in out,
        "Expected persisted assessment to be REJECT.",
    )
    require(
        "Lifecycle action: BLOCK" in out,
        "Expected lifecycle action BLOCK.",
    )
    require(
        "Instrument selection blocked." in out,
        "Expected Instrument Selection to be blocked.",
    )

    forbidden = (
        "=== INSTRUMENT SELECTOR ===",
        "Instrument candidates saved",
        "Selected instrument",
    )
    for marker in forbidden:
        require(
            marker not in out,
            f"BLOCK path unexpectedly reached downstream marker: {marker}",
        )

    print("\n=== PF-1F.4 ACCEPTANCE PASS ===")
    print(
        "Explicit persisted assessment is required, the deterministic "
        "lifecycle gate executes before Instrument Selection, and the "
        "known REJECT assessment fails closed."
    )


if __name__ == "__main__":
    main()
