"""SQLite persistence for E2E-S2.2G orchestration records."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunStage,
    DryRunStageRecord,
    DryRunStageStatus,
    SelectedOpportunityDryRun,
    SelectedOpportunityDryRunRequest,
)


class SelectedOpportunityDryRunStore:
    """Persists orchestration state alongside the existing Stage 3 store."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS selected_opportunity_dry_runs (
                    run_id TEXT PRIMARY KEY,
                    scanner_research_run_id TEXT NOT NULL,
                    opportunity_id TEXT,
                    portfolio_snapshot_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    terminal_reason TEXT,
                    started_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS selected_opportunity_dry_run_stages (
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, stage)
                );
                CREATE INDEX IF NOT EXISTS idx_s22g_runs_research
                    ON selected_opportunity_dry_runs(scanner_research_run_id);
            """)

    def save_run(
        self,
        run: SelectedOpportunityDryRun,
        request: SelectedOpportunityDryRunRequest,
    ) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO selected_opportunity_dry_runs (
                    run_id, scanner_research_run_id, opportunity_id,
                    portfolio_snapshot_id, status, terminal_reason,
                    started_at, request_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    terminal_reason=excluded.terminal_reason,
                    payload_json=excluded.payload_json
            """, (
                run.run_id,
                run.scanner_research_run_id,
                run.opportunity_id,
                run.portfolio_snapshot_id,
                run.status.value,
                run.terminal_reason.value if run.terminal_reason else None,
                run.started_at.isoformat(),
                request.model_dump_json(),
                run.model_dump_json(),
            ))

    def get_run(self, run_id: str) -> SelectedOpportunityDryRun | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM selected_opportunity_dry_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else SelectedOpportunityDryRun.model_validate_json(
            row["payload_json"]
        )

    def get_request(self, run_id: str) -> SelectedOpportunityDryRunRequest | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM selected_opportunity_dry_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else SelectedOpportunityDryRunRequest.model_validate_json(
            row["request_json"]
        )

    def save_stage(self, value: DryRunStageRecord) -> None:
        self.initialize()
        existing = self.get_stage(value.run_id, value.stage)
        if existing is not None and existing.status is DryRunStageStatus.COMPLETED:
            if existing != value:
                raise ValueError(
                    f"completed dry-run stage is immutable: {value.run_id}:{value.stage.value}"
                )
            return
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO selected_opportunity_dry_run_stages (
                    run_id, stage, status, completed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, stage) DO UPDATE SET
                    status=excluded.status,
                    completed_at=excluded.completed_at,
                    payload_json=excluded.payload_json
            """, (
                value.run_id,
                value.stage.value,
                value.status.value,
                value.completed_at.isoformat(),
                value.model_dump_json(),
            ))

    def get_stage(
        self,
        run_id: str,
        stage: DryRunStage,
    ) -> DryRunStageRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("""
                SELECT payload_json
                FROM selected_opportunity_dry_run_stages
                WHERE run_id = ? AND stage = ?
            """, (run_id, stage.value)).fetchone()
        return None if row is None else DryRunStageRecord.model_validate_json(
            row["payload_json"]
        )

    def list_stages(self, run_id: str) -> list[DryRunStageRecord]:
        self.initialize()
        order = {stage.value: index for index, stage in enumerate(DryRunStage)}
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT payload_json
                FROM selected_opportunity_dry_run_stages
                WHERE run_id = ?
            """, (run_id,)).fetchall()
        values = [DryRunStageRecord.model_validate_json(row["payload_json"]) for row in rows]
        return sorted(values, key=lambda value: order[value.stage.value])
