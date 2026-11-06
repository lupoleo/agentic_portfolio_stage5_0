from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.pf1f5_live_gate import _find_current_advance_assessment


def test_find_current_advance_assessment_prefers_latest_matching_row(tmp_path):
    db = tmp_path / "state.db"

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE portfolio_fit_assessments (
                assessment_id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decision TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO portfolio_fit_assessments (
                assessment_id,
                opportunity_id,
                snapshot_id,
                created_at,
                decision
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("A-OLD", "OPP-1", "SNAP-1", "2026-09-13T10:00:00+00:00", "PASS"),
                ("A-REJECT", "OPP-2", "SNAP-2", "2026-09-13T11:00:00+00:00", "REJECT"),
                ("A-WARN", "OPP-3", "SNAP-2", "2026-09-13T12:00:00+00:00", "PASS_WITH_WARNING"),
                ("A-PASS", "OPP-4", "SNAP-2", "2026-09-13T13:00:00+00:00", "PASS"),
            ],
        )

    assert _find_current_advance_assessment(
        str(db),
        "SNAP-2",
    ) == ("A-PASS", "OPP-4")


def test_find_current_advance_assessment_returns_none_without_current_pass(tmp_path):
    db = tmp_path / "state.db"

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE portfolio_fit_assessments (
                assessment_id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decision TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO portfolio_fit_assessments (
                assessment_id,
                opportunity_id,
                snapshot_id,
                created_at,
                decision
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "A-REJECT",
                "OPP-1",
                "SNAP-2",
                "2026-09-13T10:00:00+00:00",
                "REJECT",
            ),
        )

    assert _find_current_advance_assessment(
        str(db),
        "SNAP-2",
    ) is None
