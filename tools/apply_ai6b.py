from __future__ import annotations

from pathlib import Path

STORAGE = Path("app/cio/storage.py")
IMPORT_LINE = "from app.ai.research_models import OpportunityResearch\n"

TABLE_SQL = r'''
                CREATE TABLE IF NOT EXISTS opportunity_research (
                    research_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    scan_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    research_status TEXT NOT NULL,
                    evidence_quality TEXT NOT NULL,
                    research_confidence REAL NOT NULL,
                    portfolio_snapshot_id TEXT,
                    risk_state_id TEXT,
                    requires_additional_research INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_opportunity_research_candidate
                    ON opportunity_research(candidate_id);
                CREATE INDEX IF NOT EXISTS idx_opportunity_research_scan
                    ON opportunity_research(scan_id);
                CREATE INDEX IF NOT EXISTS idx_opportunity_research_created
                    ON opportunity_research(created_at);
                CREATE INDEX IF NOT EXISTS idx_opportunity_research_ticker
                    ON opportunity_research(ticker);
                CREATE INDEX IF NOT EXISTS idx_opportunity_research_status
                    ON opportunity_research(research_status);
                CREATE INDEX IF NOT EXISTS idx_opportunity_research_quality
                    ON opportunity_research(evidence_quality);
                CREATE INDEX IF NOT EXISTS idx_opportunity_research_snapshot
                    ON opportunity_research(portfolio_snapshot_id);
                CREATE INDEX IF NOT EXISTS idx_opportunity_research_risk_state
                    ON opportunity_research(risk_state_id);

'''

METHODS = r'''
    # =========================================================
    # Opportunity Research
    # =========================================================

    def save_opportunity_research(
        self,
        research: OpportunityResearch,
    ) -> None:
        """Persist research while enforcing ScanCandidate provenance."""

        self.initialize()
        candidate = self.get_scan_candidate(research.candidate_id)

        if candidate is None:
            raise ValueError(
                "ScanCandidate not found: "
                f"{research.candidate_id}"
            )
        if research.scan_id != candidate.scan_id:
            raise ValueError(
                "OpportunityResearch scan_id does not match "
                "ScanCandidate scan_id"
            )
        if research.ticker != candidate.ticker:
            raise ValueError(
                "OpportunityResearch ticker does not match "
                "ScanCandidate ticker"
            )
        if (
            research.portfolio_snapshot_id
            != candidate.portfolio_snapshot_id
        ):
            raise ValueError(
                "OpportunityResearch portfolio_snapshot_id "
                "does not match ScanCandidate"
            )
        if research.risk_state_id != candidate.risk_state_id:
            raise ValueError(
                "OpportunityResearch risk_state_id does not "
                "match ScanCandidate"
            )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO opportunity_research (
                    research_id, candidate_id, scan_id, created_at,
                    ticker, research_status, evidence_quality,
                    research_confidence, portfolio_snapshot_id,
                    risk_state_id, requires_additional_research,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(research_id)
                DO UPDATE SET
                    candidate_id = excluded.candidate_id,
                    scan_id = excluded.scan_id,
                    created_at = excluded.created_at,
                    ticker = excluded.ticker,
                    research_status = excluded.research_status,
                    evidence_quality = excluded.evidence_quality,
                    research_confidence = excluded.research_confidence,
                    portfolio_snapshot_id = excluded.portfolio_snapshot_id,
                    risk_state_id = excluded.risk_state_id,
                    requires_additional_research =
                        excluded.requires_additional_research,
                    payload_json = excluded.payload_json
                """,
                (
                    research.research_id,
                    research.candidate_id,
                    research.scan_id,
                    research.created_at.isoformat(),
                    research.ticker,
                    research.research_status.value,
                    research.evidence_quality.value,
                    research.research_confidence,
                    research.portfolio_snapshot_id,
                    research.risk_state_id,
                    int(research.requires_additional_research),
                    research.model_dump_json(),
                ),
            )

    def get_opportunity_research(
        self,
        research_id: str,
    ) -> OpportunityResearch | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM opportunity_research
                WHERE research_id = ?
                """,
                (research_id,),
            ).fetchone()
        if row is None:
            return None
        return OpportunityResearch.model_validate_json(
            row["payload_json"]
        )

    def list_opportunity_research(
        self,
        *,
        candidate_id: str | None = None,
        scan_id: str | None = None,
        ticker: str | None = None,
        research_status: str | None = None,
        evidence_quality: str | None = None,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
        requires_additional_research: bool | None = None,
    ) -> list[OpportunityResearch]:
        self.initialize()
        clauses: list[str] = []
        params: list[object] = []

        filters = (
            ("candidate_id", candidate_id),
            ("scan_id", scan_id),
            ("research_status", research_status),
            ("evidence_quality", evidence_quality),
            ("portfolio_snapshot_id", portfolio_snapshot_id),
            ("risk_state_id", risk_state_id),
        )
        for column, value in filters:
            if value is not None:
                if hasattr(value, "value"):
                    value = value.value
                clauses.append(f"{column} = ?")
                params.append(value)

        if ticker is not None:
            clauses.append("UPPER(ticker) = ?")
            params.append(ticker.upper())

        if requires_additional_research is not None:
            clauses.append("requires_additional_research = ?")
            params.append(int(requires_additional_research))

        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM opportunity_research
                {where_sql}
                ORDER BY created_at DESC, research_id DESC
                """,
                tuple(params),
            ).fetchall()

        return [
            OpportunityResearch.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def list_research_for_candidate(
        self,
        candidate_id: str,
    ) -> list[OpportunityResearch]:
        return self.list_opportunity_research(
            candidate_id=candidate_id
        )

    def get_latest_opportunity_research(
        self,
        *,
        candidate_id: str | None = None,
        scan_id: str | None = None,
        ticker: str | None = None,
        research_status: str | None = None,
        evidence_quality: str | None = None,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
        requires_additional_research: bool | None = None,
    ) -> OpportunityResearch | None:
        records = self.list_opportunity_research(
            candidate_id=candidate_id,
            scan_id=scan_id,
            ticker=ticker,
            research_status=research_status,
            evidence_quality=evidence_quality,
            portfolio_snapshot_id=portfolio_snapshot_id,
            risk_state_id=risk_state_id,
            requires_additional_research=requires_additional_research,
        )
        return records[0] if records else None

'''

def main() -> None:
    if not STORAGE.exists():
        raise SystemExit(f"Run from project root; not found: {STORAGE}")

    original = STORAGE.read_text(encoding="utf-8")
    text = original

    required = (
        "def save_market_scan(",
        "def save_scan_candidate(",
        "CREATE TABLE IF NOT EXISTS market_scans",
        "CREATE TABLE IF NOT EXISTS scan_candidates",
    )
    missing = [x for x in required if x not in text]
    if missing:
        raise SystemExit(
            "AI-6B patch refused: expected AI-5B baseline missing: "
            + ", ".join(missing)
        )

    if "def save_opportunity_research(" in text:
        print("AI-6B already applied; no changes made.")
        return

    if IMPORT_LINE.strip() not in text:
        anchor = "from app.ai import AIInferenceRecord\n"
        if anchor not in text:
            raise SystemExit("Cannot locate AIInferenceRecord import.")
        text = text.replace(anchor, anchor + IMPORT_LINE, 1)

    table_anchor = (
        "                CREATE TABLE IF NOT EXISTS "
        "trade_opportunities ("
    )
    if table_anchor not in text:
        raise SystemExit("Cannot locate trade_opportunities schema.")
    text = text.replace(table_anchor, TABLE_SQL + table_anchor, 1)

    method_anchor = (
        "    # =========================================================\n"
        "    # Trade Opportunities\n"
        "    # =========================================================\n"
    )
    if method_anchor not in text:
        raise SystemExit("Cannot locate Trade Opportunities section.")
    text = text.replace(method_anchor, METHODS + method_anchor, 1)

    compile(text, str(STORAGE), "exec")

    backup = STORAGE.with_suffix(".py.ai6b_backup")
    backup.write_text(original, encoding="utf-8")
    STORAGE.write_text(text, encoding="utf-8")

    print("AI-6B applied successfully.")
    print(f"Backup: {backup}")

if __name__ == "__main__":
    main()
