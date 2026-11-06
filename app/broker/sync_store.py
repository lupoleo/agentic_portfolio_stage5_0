"""Append-only SQLite persistence of account sync runs and state versions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import PortfolioMode, StateChange, StateVersion, SyncResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS state_version (
    version_id TEXT PRIMARY KEY,
    parent_version_id TEXT,
    mode TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    structural_fingerprint TEXT NOT NULL,
    account_fingerprint TEXT NOT NULL,
    profile_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_run (
    sync_id TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    outcome TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES state_version(version_id),
    changes_json TEXT NOT NULL,
    broker_orders_submitted INTEGER NOT NULL,
    portfolio_mutations INTEGER NOT NULL,
    automatic_executions INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS state_version_no_update BEFORE UPDATE ON state_version
BEGIN SELECT RAISE(ABORT, 'state_version is append-only'); END;
CREATE TRIGGER IF NOT EXISTS state_version_no_delete BEFORE DELETE ON state_version
BEGIN SELECT RAISE(ABORT, 'state_version is append-only'); END;
CREATE TRIGGER IF NOT EXISTS sync_run_no_update BEFORE UPDATE ON sync_run
BEGIN SELECT RAISE(ABORT, 'sync_run is append-only'); END;
CREATE TRIGGER IF NOT EXISTS sync_run_no_delete BEFORE DELETE ON sync_run
BEGIN SELECT RAISE(ABORT, 'sync_run is append-only'); END;
"""


class SyncStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with self._connect() as con:
            con.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path)
        con.row_factory = sqlite3.Row
        return con

    def latest_version(self, mode: PortfolioMode) -> StateVersion | None:
        """Latest version of one lineage. Modes never share a lineage."""

        with self._connect() as con:
            row = con.execute(
                "SELECT payload_json FROM state_version WHERE mode = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (mode.value,),
            ).fetchone()
        return None if row is None else StateVersion.model_validate_json(row["payload_json"])

    def get_version(self, version_id: str) -> StateVersion:
        with self._connect() as con:
            row = con.execute("SELECT payload_json FROM state_version WHERE version_id = ?", (version_id,)).fetchone()
        if row is None:
            raise KeyError(version_id)
        return StateVersion.model_validate_json(row["payload_json"])

    def record(self, result: SyncResult, *, new_version: bool) -> None:
        v = result.version
        with self._connect() as con:
            if new_version:
                con.execute(
                    "INSERT INTO state_version VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        v.version_id,
                        v.parent_version_id,
                        v.mode.value,
                        v.origin.value,
                        v.created_at.isoformat(),
                        v.structural_fingerprint,
                        v.account_fingerprint,
                        v.profile_fingerprint,
                        v.model_dump_json(),
                    ),
                )
            con.execute(
                "INSERT INTO sync_run VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    result.sync_id,
                    result.observed_at.isoformat(),
                    v.mode.value,
                    result.outcome.value,
                    v.version_id,
                    json.dumps([c.model_dump(mode="json") for c in result.changes]),
                    result.broker_orders_submitted,
                    result.portfolio_mutations,
                    result.automatic_executions,
                ),
            )

    def sync_runs(self, mode: PortfolioMode) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT sync_id, observed_at, outcome, version_id, changes_json FROM sync_run WHERE mode = ? ORDER BY rowid",
                (mode.value,),
            ).fetchall()
        return [
            {
                "sync_id": r["sync_id"],
                "observed_at": r["observed_at"],
                "outcome": r["outcome"],
                "version_id": r["version_id"],
                "changes": [StateChange.model_validate(c) for c in json.loads(r["changes_json"])],
            }
            for r in rows
        ]
