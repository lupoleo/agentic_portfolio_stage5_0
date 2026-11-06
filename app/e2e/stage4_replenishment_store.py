"""Append-only SQLite evidence for E2E-S4.0A.1 replenishment waves."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.e2e.stage4_contracts import canonical_payload
from app.e2e.stage4_replenishment_contracts import (
    CandidateReplenishmentSession,
    CandidateWaveRecord,
)
from app.e2e.stage4_frontier_priority import FrontierRankingSnapshot


class Stage4CandidateReplenishmentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS stage4_candidate_replenishment_sessions (
                    session_id TEXT PRIMARY KEY,
                    root_run_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    session_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stage4_candidate_replenishment_waves (
                    record_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    wave_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    UNIQUE(session_id, wave_index, record_id),
                    FOREIGN KEY(session_id)
                        REFERENCES stage4_candidate_replenishment_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_stage4_candidate_waves_session
                ON stage4_candidate_replenishment_waves (
                    session_id, wave_index, record_id
                );
                CREATE TABLE IF NOT EXISTS stage4_frontier_ranking_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    session_id TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    FOREIGN KEY(session_id)
                        REFERENCES stage4_candidate_replenishment_sessions(
                            session_id
                        )
                );
            """)

    def save_session(self, value: CandidateReplenishmentSession) -> None:
        existing = self.get_session(value.session_id)
        if existing is not None:
            if existing == value:
                return
            raise ValueError("candidate replenishment session is immutable")
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO stage4_candidate_replenishment_sessions (
                    session_id, root_run_id, fingerprint, session_json
                ) VALUES (?, ?, ?, ?)
            """, (
                value.session_id,
                value.root_run_id,
                value.fingerprint,
                canonical_payload(value),
            ))

    def get_session(self, session_id: str):
        with self._connect() as connection:
            row = connection.execute("""
                SELECT session_json
                FROM stage4_candidate_replenishment_sessions
                WHERE session_id = ?
            """, (session_id,)).fetchone()
        return (
            None if row is None
            else CandidateReplenishmentSession.model_validate_json(row[0])
        )

    def save_wave(self, value: CandidateWaveRecord) -> None:
        if self.get_session(value.session_id) is None:
            raise ValueError("wave cannot be saved before its session")
        existing = self.get_wave(value.record_id)
        if existing is not None:
            if existing == value:
                return
            raise ValueError("candidate replenishment wave is immutable")
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO stage4_candidate_replenishment_waves (
                    record_id, session_id, wave_index,
                    status, fingerprint, record_json
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                value.record_id,
                value.session_id,
                value.wave_index,
                value.status.value,
                value.fingerprint,
                canonical_payload(value),
            ))

    def get_wave(self, record_id: str):
        with self._connect() as connection:
            row = connection.execute("""
                SELECT record_json
                FROM stage4_candidate_replenishment_waves
                WHERE record_id = ?
            """, (record_id,)).fetchone()
        return None if row is None else CandidateWaveRecord.model_validate_json(row[0])

    def get_wave_by_plan_fingerprint(
        self,
        session_id: str,
        plan_fingerprint: str,
    ):
        matches = tuple(
            value
            for value in self.list_waves(session_id)
            if value.plan_fingerprint == plan_fingerprint
        )
        if len(matches) > 1:
            raise ValueError(
                "multiple immutable wave records share a plan fingerprint"
            )
        return matches[0] if matches else None

    def list_waves(self, session_id: str) -> tuple[CandidateWaveRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT record_json
                FROM stage4_candidate_replenishment_waves
                WHERE session_id = ?
                ORDER BY wave_index, record_id
            """, (session_id,)).fetchall()
        values = tuple(
            CandidateWaveRecord.model_validate_json(row[0])
            for row in rows
        )
        return tuple(sorted(
            values,
            key=lambda value: (
                value.wave_index,
                value.started_at,
                value.record_id,
            ),
        ))

    def save_frontier_ranking(
        self,
        value: FrontierRankingSnapshot,
    ) -> None:
        if self.get_session(value.session_id) is None:
            raise ValueError(
                "frontier ranking cannot be saved before its session"
            )
        existing = self.get_frontier_ranking(value.session_id)
        if existing is not None:
            if existing == value:
                return
            raise ValueError("frontier ranking snapshot is immutable")
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO stage4_frontier_ranking_snapshots (
                    snapshot_id, session_id, fingerprint, snapshot_json
                ) VALUES (?, ?, ?, ?)
            """, (
                value.snapshot_id,
                value.session_id,
                value.fingerprint,
                canonical_payload(value),
            ))

    def get_frontier_ranking(
        self,
        session_id: str,
    ) -> FrontierRankingSnapshot | None:
        with self._connect() as connection:
            row = connection.execute("""
                SELECT snapshot_json
                FROM stage4_frontier_ranking_snapshots
                WHERE session_id = ?
            """, (session_id,)).fetchone()
        return (
            None if row is None
            else FrontierRankingSnapshot.model_validate_json(row[0])
        )
