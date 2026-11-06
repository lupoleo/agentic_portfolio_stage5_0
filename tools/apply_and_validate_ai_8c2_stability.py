
from __future__ import annotations

import os
import subprocess
import sys


def run(cmd):
    print()
    print(">", " ".join(cmd))
    completed = subprocess.run(cmd)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main():
    run([sys.executable, "tools/apply_ai_8c2_consolidated_stability.py"])

    tests = [
        "tests/test_ai_local_provider.py",
        "tests/test_ai_opportunity_scoring_service.py",
    ]
    extra = "tests/test_ai_research_service_unknown_conflict_canonicalization.py"
    if os.path.exists(extra):
        tests.append(extra)

    run([sys.executable, "-m", "pytest", *tests, "-q"])

    print()
    print("Focused tests passed. Starting 12 x 2 live validation matrix.")
    run([sys.executable, "-m", "tools.batch_validate_opportunity_scoring_8c2"])


if __name__ == "__main__":
    main()
