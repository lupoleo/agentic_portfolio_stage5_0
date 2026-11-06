"""SQLite persistence for E2E-S4.0A manifests and stage evidence."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.e2e.stage4_contracts import (
    Stage4E2ERequest,
    Stage4E2ERun,
    IMMUTABLE_RUN_STATUSES,
    Stage4Stage,
    Stage4StageRecord,
    Stage4StageStatus,
    canonical_fingerprint,
    canonical_payload,
    stage4_request_fingerprint,
)


class Stage4E2EStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS stage4_e2e_runs (
                    run_id TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    run_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    run_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stage4_e2e_stages (
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    output_fingerprint TEXT,
                    record_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, stage),
                    FOREIGN KEY (run_id) REFERENCES stage4_e2e_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS stage4_e2e_stage_attempts (
                    attempt_fingerprint TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    output_fingerprint TEXT,
                    completed_at TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES stage4_e2e_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS
                    idx_stage4_e2e_stage_attempts_run
                ON stage4_e2e_stage_attempts (
                    run_id, completed_at, stage
                );
                """
            )

    def save_run(self, run: Stage4E2ERun, request: Stage4E2ERequest) -> None:
        expected = stage4_request_fingerprint(request)
        if run.request_fingerprint != expected:
            raise ValueError("run/request fingerprint mismatch")
        existing = self.get_run(run.run_id)
        if existing is not None:
            if existing.request_fingerprint != expected:
                raise ValueError("run identity collision with different request")
            if existing.status in IMMUTABLE_RUN_STATUSES:
                if existing.fingerprint == run.fingerprint:
                    return
                raise ValueError("terminal Stage 4 run is immutable")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO stage4_e2e_runs (
                    run_id, request_fingerprint, request_json,
                    run_fingerprint, status, run_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    request_fingerprint=excluded.request_fingerprint,
                    request_json=excluded.request_json,
                    run_fingerprint=excluded.run_fingerprint,
                    status=excluded.status,
                    run_json=excluded.run_json
                """,
                (
                    run.run_id,
                    expected,
                    canonical_payload(request),
                    run.fingerprint,
                    run.status.value,
                    canonical_payload(run),
                ),
            )

    def get_run(self, run_id: str) -> Stage4E2ERun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_json FROM stage4_e2e_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return Stage4E2ERun.model_validate_json(row[0]) if row else None

    def get_request(self, run_id: str) -> Stage4E2ERequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM stage4_e2e_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return Stage4E2ERequest.model_validate_json(row[0]) if row else None

    def save_stage(self, record: Stage4StageRecord) -> None:
        if self.get_run(record.run_id) is None:
            raise ValueError("stage cannot be saved before its parent run")
        existing = self.get_stage(record.run_id, record.stage)
        if existing is not None:
            if existing == record:
                return
            raise ValueError("terminal Stage 4 stage record is immutable")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO stage4_e2e_stages (
                    run_id, stage, status, input_fingerprint,
                    output_fingerprint, record_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.stage.value,
                    record.status.value,
                    record.input_fingerprint,
                    record.output_fingerprint,
                    canonical_payload(record),
                ),
            )


    def save_attempt(self, record: Stage4StageRecord) -> None:
        if record.status is not Stage4StageStatus.PARTIAL:
            raise ValueError(
                "stage attempt persistence accepts only PARTIAL records"
            )
        if self.get_run(record.run_id) is None:
            raise ValueError(
                "stage attempt cannot be saved before its parent run"
            )
        attempt_fingerprint = canonical_fingerprint(record)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO stage4_e2e_stage_attempts (
                    attempt_fingerprint, run_id, stage, status,
                    input_fingerprint, output_fingerprint,
                    completed_at, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_fingerprint,
                    record.run_id,
                    record.stage.value,
                    record.status.value,
                    record.input_fingerprint,
                    record.output_fingerprint,
                    record.completed_at.isoformat(),
                    canonical_payload(record),
                ),
            )

    def list_attempts(
        self,
        run_id: str,
    ) -> tuple[Stage4StageRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record_json
                FROM stage4_e2e_stage_attempts
                WHERE run_id = ?
                ORDER BY completed_at, stage, attempt_fingerprint
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            Stage4StageRecord.model_validate_json(row[0])
            for row in rows
        )

    def get_stage(
        self, run_id: str, stage: Stage4Stage
    ) -> Stage4StageRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT record_json FROM stage4_e2e_stages
                WHERE run_id = ? AND stage = ?
                """,
                (run_id, stage.value),
            ).fetchone()
        return Stage4StageRecord.model_validate_json(row[0]) if row else None

    def list_stages(self, run_id: str) -> tuple[Stage4StageRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record_json FROM stage4_e2e_stages WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        records = [Stage4StageRecord.model_validate_json(row[0]) for row in rows]
        order = {stage: index for index, stage in enumerate(Stage4Stage)}
        return tuple(sorted(records, key=lambda item: order[item.stage]))
