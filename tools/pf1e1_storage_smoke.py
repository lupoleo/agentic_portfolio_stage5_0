from pathlib import Path
import sqlite3
import tempfile

from app.cio.storage import Stage3Store


with tempfile.TemporaryDirectory() as d:
    db = Path(d) / "pf1e1.sqlite"

    store = Stage3Store(db)
    store.initialize()

    con = sqlite3.connect(db)

    try:
        cols = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info(portfolio_fit_assessments)"
            ).fetchall()
        }
    finally:
        # Important on Windows: sqlite3's context manager
        # commits/rolls back but does not necessarily close
        # the underlying file handle.
        con.close()

    required = {
        "assessment_id",
        "opportunity_id",
        "snapshot_id",
        "risk_state_id",
        "account_state_id",
        "created_at",
        "ticker",
        "direction",
        "decision",
        "portfolio_fit_score",
        "scoring_coverage_pct",
        "analytical_coverage_pct",
        "payload_json",
    }

    missing = required - cols

    if missing:
        raise SystemExit(
            f"FAIL missing columns: {sorted(missing)}"
        )


for name in (
    "save_portfolio_fit_assessment",
    "get_portfolio_fit_assessment",
    "get_latest_portfolio_fit_assessment",
    "list_portfolio_fit_assessments",
):
    if not hasattr(Stage3Store, name):
        raise SystemExit(
            f"FAIL missing Stage3Store method: {name}"
        )


print("PF-1E.1 schema/API smoke: PASS")
