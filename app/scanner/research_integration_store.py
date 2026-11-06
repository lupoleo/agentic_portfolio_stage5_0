"""SQLite persistence for E2E-S2.2F integration-specific records."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.ai.opportunity_score_models import OpportunityScore
from app.scanner.research_integration_contracts import (
    PersistedEvidenceBundle,
    ResearchHypothesisOutcome,
    ScannerResearchRun,
    TradeOpportunityProvenanceLink,
)


class ScannerResearchIntegrationStore:
    """Coexists with Stage3Store in the same SQLite database."""

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
                CREATE TABLE IF NOT EXISTS scanner_research_runs (
                    run_id TEXT PRIMARY KEY,
                    watch_universe_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scanner_research_outcomes (
                    integration_run_id TEXT NOT NULL,
                    hypothesis_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(integration_run_id, hypothesis_id)
                );
                CREATE TABLE IF NOT EXISTS scanner_evidence_bundles (
                    bundle_id TEXT PRIMARY KEY,
                    integration_run_id TEXT NOT NULL,
                    hypothesis_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS opportunity_scores (
                    opportunity_score_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    research_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    scoring_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trade_opportunity_provenance (
                    opportunity_id TEXT PRIMARY KEY,
                    integration_run_id TEXT NOT NULL,
                    hypothesis_id TEXT NOT NULL,
                    research_id TEXT NOT NULL,
                    opportunity_score_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sri_outcomes_run
                    ON scanner_research_outcomes(integration_run_id);
                CREATE INDEX IF NOT EXISTS idx_sri_bundles_run
                    ON scanner_evidence_bundles(integration_run_id);
                CREATE INDEX IF NOT EXISTS idx_sri_scores_candidate
                    ON opportunity_scores(candidate_id);
                CREATE INDEX IF NOT EXISTS idx_sri_provenance_run
                    ON trade_opportunity_provenance(integration_run_id);
            """)

    def _save(self, sql: str, params: tuple) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(sql, params)

    def _get(self, table: str, key_column: str, key: str, model):
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {table} WHERE {key_column} = ?", (key,)
            ).fetchone()
        return None if row is None else model.model_validate_json(row["payload_json"])

    def save_run(self, value: ScannerResearchRun) -> None:
        self._save("""
            INSERT INTO scanner_research_runs
                (run_id, watch_universe_fingerprint, status, started_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                watch_universe_fingerprint=excluded.watch_universe_fingerprint,
                status=excluded.status,
                started_at=excluded.started_at,
                payload_json=excluded.payload_json
        """, (
            value.run_id, value.watch_universe_fingerprint, value.status.value,
            value.started_at.isoformat(), value.model_dump_json(),
        ))

    def get_run(self, run_id: str) -> ScannerResearchRun | None:
        return self._get("scanner_research_runs", "run_id", run_id, ScannerResearchRun)

    def save_outcome(self, value: ResearchHypothesisOutcome) -> None:
        self._save("""
            INSERT INTO scanner_research_outcomes
                (integration_run_id, hypothesis_id, status, reason, updated_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(integration_run_id, hypothesis_id) DO UPDATE SET
                status=excluded.status, reason=excluded.reason,
                updated_at=excluded.updated_at, payload_json=excluded.payload_json
        """, (
            value.integration_run_id, value.hypothesis_id, value.status.value,
            value.reason.value, value.updated_at.isoformat(), value.model_dump_json(),
        ))

    def get_outcome(self, run_id: str, hypothesis_id: str) -> ResearchHypothesisOutcome | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("""
                SELECT payload_json FROM scanner_research_outcomes
                WHERE integration_run_id = ? AND hypothesis_id = ?
            """, (run_id, hypothesis_id)).fetchone()
        return None if row is None else ResearchHypothesisOutcome.model_validate_json(row["payload_json"])

    def list_outcomes(self, run_id: str) -> list[ResearchHypothesisOutcome]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT payload_json FROM scanner_research_outcomes
                WHERE integration_run_id = ? ORDER BY hypothesis_id
            """, (run_id,)).fetchall()
        return [ResearchHypothesisOutcome.model_validate_json(row["payload_json"]) for row in rows]

    def save_evidence_bundle(self, value: PersistedEvidenceBundle) -> None:
        self._save("""
            INSERT INTO scanner_evidence_bundles
                (bundle_id, integration_run_id, hypothesis_id, created_at, fingerprint, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(bundle_id) DO UPDATE SET payload_json=excluded.payload_json
        """, (
            value.bundle_id, value.integration_run_id, value.hypothesis_id,
            value.created_at.isoformat(), value.fingerprint, value.model_dump_json(),
        ))

    def get_evidence_bundle(self, bundle_id: str) -> PersistedEvidenceBundle | None:
        return self._get(
            "scanner_evidence_bundles", "bundle_id", bundle_id, PersistedEvidenceBundle,
        )

    def save_opportunity_score(self, value: OpportunityScore) -> None:
        self._save("""
            INSERT INTO opportunity_scores
                (opportunity_score_id, candidate_id, research_id, created_at, scoring_status, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(opportunity_score_id) DO UPDATE SET payload_json=excluded.payload_json
        """, (
            value.opportunity_score_id, value.candidate_id, value.research_id,
            value.created_at.isoformat(), value.scoring_status.value, value.model_dump_json(),
        ))

    def get_opportunity_score(self, score_id: str) -> OpportunityScore | None:
        return self._get("opportunity_scores", "opportunity_score_id", score_id, OpportunityScore)

    def save_provenance_link(self, value: TradeOpportunityProvenanceLink) -> None:
        self._save("""
            INSERT INTO trade_opportunity_provenance
                (opportunity_id, integration_run_id, hypothesis_id, research_id,
                 opportunity_score_id, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(opportunity_id) DO UPDATE SET payload_json=excluded.payload_json
        """, (
            value.opportunity_id, value.integration_run_id, value.hypothesis_id,
            value.research_id, value.opportunity_score_id,
            value.created_at.isoformat(), value.model_dump_json(),
        ))

    def get_provenance_link(self, opportunity_id: str) -> TradeOpportunityProvenanceLink | None:
        return self._get(
            "trade_opportunity_provenance", "opportunity_id", opportunity_id,
            TradeOpportunityProvenanceLink,
        )
