from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.ai import AIInferenceRecord
from app.ai.research_models import OpportunityResearch
from app.ai.scan_models import MarketScan, ScanCandidate
from app.cio.models import (
    AccountState,
    CioDecision,
    ExposureRelationship,
    ExecutionPlan,
    FinecoInstrument,
    InstrumentCandidate,
    OperatorConfirmation,
    OpportunityStatus,
    PortfolioSnapshot,
    PortfolioRiskStateRecord,
    PortfolioSimulation,
    TradeOpportunity,
    PositionSizingResult,
    TradeProposal,
    TradeOutcome,
)


class Stage3Store:
    """
    Local SQLite persistence for Stage 3 canonical state.

    Fineco remains the authoritative source for real portfolio positions
    and actual order execution.

    This store persists:

        - CIO Account State
        - Portfolio analytical snapshots
        - Fineco Instrument Cache

    It is deliberately NOT a broker ledger.
    """

    def __init__(
        self,
        db_path: str | Path,
    ):
        self.db_path = Path(db_path)

    # =========================================================
    # Database initialization / migration
    # =========================================================

    def initialize(self) -> None:
        """
        Create the Stage 3 database when necessary and apply
        forward-compatible schema migrations.

        The canonical object remains payload_json.

        Selected FinecoInstrument fields are also stored as
        relational columns because they will be useful for
        Instrument Selector queries.
        """

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with self._connect() as connection:

            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS account_states (
                    account_state_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_account_states_timestamp
                    ON account_states(timestamp);


                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    source_file_hash TEXT NOT NULL,
                    quant_engine_version TEXT NOT NULL,
                    account_state_id TEXT,
                    payload_json TEXT NOT NULL,

                    FOREIGN KEY(account_state_id)
                        REFERENCES account_states(account_state_id)
                );

                CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_timestamp
                    ON portfolio_snapshots(timestamp);

                CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_hash
                    ON portfolio_snapshots(source_file_hash);

                CREATE TABLE IF NOT EXISTS portfolio_risk_states (
                    risk_state_id TEXT PRIMARY KEY,

                    snapshot_id TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS
                idx_portfolio_risk_states_snapshot
                ON portfolio_risk_states(snapshot_id);

                CREATE INDEX IF NOT EXISTS
                idx_portfolio_risk_states_created
                ON portfolio_risk_states(created_at);

                CREATE TABLE IF NOT EXISTS ai_inferences (
                    inference_id TEXT PRIMARY KEY,

                    timestamp TEXT NOT NULL,

                    task TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,

                    sensitivity TEXT NOT NULL,
                    reasoning_mode TEXT NOT NULL,

                    portfolio_snapshot_id TEXT,
                    risk_state_id TEXT,

                    validation_status TEXT NOT NULL,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_ai_inferences_timestamp
                    ON ai_inferences(timestamp);

                CREATE INDEX IF NOT EXISTS idx_ai_inferences_task
                    ON ai_inferences(task);

                CREATE INDEX IF NOT EXISTS idx_ai_inferences_provider_model
                    ON ai_inferences(provider, model);

                CREATE INDEX IF NOT EXISTS idx_ai_inferences_snapshot
                    ON ai_inferences(portfolio_snapshot_id);

                CREATE INDEX IF NOT EXISTS idx_ai_inferences_risk_state
                    ON ai_inferences(risk_state_id);

                CREATE TABLE IF NOT EXISTS market_scans (
                    scan_id TEXT PRIMARY KEY,

                    created_at TEXT NOT NULL,
                    scanner_type TEXT NOT NULL,
                    scanner_version TEXT NOT NULL,
                    universe_type TEXT NOT NULL,
                    status TEXT NOT NULL,

                    portfolio_snapshot_id TEXT,
                    risk_state_id TEXT,

                    started_at TEXT NOT NULL,
                    completed_at TEXT,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_market_scans_created
                    ON market_scans(created_at);

                CREATE INDEX IF NOT EXISTS idx_market_scans_scanner_type
                    ON market_scans(scanner_type);

                CREATE INDEX IF NOT EXISTS idx_market_scans_universe_type
                    ON market_scans(universe_type);

                CREATE INDEX IF NOT EXISTS idx_market_scans_status
                    ON market_scans(status);

                CREATE INDEX IF NOT EXISTS idx_market_scans_snapshot
                    ON market_scans(portfolio_snapshot_id);

                CREATE INDEX IF NOT EXISTS idx_market_scans_risk_state
                    ON market_scans(risk_state_id);


                CREATE TABLE IF NOT EXISTS scan_candidates (
                    candidate_id TEXT PRIMARY KEY,

                    scan_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,

                    ticker TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    action TEXT NOT NULL,
                    signal_type TEXT NOT NULL,

                    raw_score REAL,
                    scanner_confidence REAL,

                    catalyst_type TEXT,

                    portfolio_snapshot_id TEXT,
                    risk_state_id TEXT,

                    requires_research INTEGER NOT NULL,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scan_candidates_scan
                    ON scan_candidates(scan_id);

                CREATE INDEX IF NOT EXISTS idx_scan_candidates_created
                    ON scan_candidates(created_at);

                CREATE INDEX IF NOT EXISTS idx_scan_candidates_ticker
                    ON scan_candidates(ticker);

                CREATE INDEX IF NOT EXISTS idx_scan_candidates_origin
                    ON scan_candidates(origin);

                CREATE INDEX IF NOT EXISTS idx_scan_candidates_action
                    ON scan_candidates(action);

                CREATE INDEX IF NOT EXISTS idx_scan_candidates_signal_type
                    ON scan_candidates(signal_type);

                CREATE INDEX IF NOT EXISTS idx_scan_candidates_snapshot
                    ON scan_candidates(portfolio_snapshot_id);

                CREATE INDEX IF NOT EXISTS idx_scan_candidates_risk_state
                    ON scan_candidates(risk_state_id);


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

                CREATE TABLE IF NOT EXISTS trade_opportunities (
                    opportunity_id TEXT PRIMARY KEY,

                    snapshot_id TEXT NOT NULL,

                    created_at TEXT NOT NULL,
                    updated_at TEXT,

                    ticker TEXT NOT NULL,

                    direction TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    status TEXT NOT NULL,

                    confidence REAL NOT NULL,

                    broker_instruments_available INTEGER,
                    broker_instrument_count INTEGER,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_trade_opportunities_ticker
                    ON trade_opportunities(ticker);

                CREATE INDEX IF NOT EXISTS idx_trade_opportunities_status
                    ON trade_opportunities(status);

                CREATE INDEX IF NOT EXISTS idx_trade_opportunities_created
                    ON trade_opportunities(created_at);

                CREATE INDEX IF NOT EXISTS idx_trade_opportunities_direction
                    ON trade_opportunities(direction);


                CREATE TABLE IF NOT EXISTS portfolio_fit_assessments (
                    assessment_id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    risk_state_id TEXT,
                    account_state_id TEXT,
                    created_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    portfolio_fit_score REAL,
                    scoring_coverage_pct REAL,
                    analytical_coverage_pct REAL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_portfolio_fit_assessments_opportunity
                    ON portfolio_fit_assessments(opportunity_id);
                CREATE INDEX IF NOT EXISTS idx_portfolio_fit_assessments_snapshot
                    ON portfolio_fit_assessments(snapshot_id);
                CREATE INDEX IF NOT EXISTS idx_portfolio_fit_assessments_risk_state
                    ON portfolio_fit_assessments(risk_state_id);
                CREATE INDEX IF NOT EXISTS idx_portfolio_fit_assessments_account_state
                    ON portfolio_fit_assessments(account_state_id);
                CREATE INDEX IF NOT EXISTS idx_portfolio_fit_assessments_created
                    ON portfolio_fit_assessments(created_at);
                CREATE INDEX IF NOT EXISTS idx_portfolio_fit_assessments_decision
                    ON portfolio_fit_assessments(decision);
                CREATE INDEX IF NOT EXISTS idx_portfolio_fit_assessments_ticker
                    ON portfolio_fit_assessments(ticker);

                CREATE TABLE IF NOT EXISTS instrument_candidates (
                    opportunity_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,

                    eligible INTEGER NOT NULL,
                    suitability_score REAL,
                    rejection_reason TEXT,

                    payload_json TEXT NOT NULL,

                    PRIMARY KEY (
                        opportunity_id,
                        instrument_id
                    )
                );

                CREATE INDEX IF NOT EXISTS
                idx_instrument_candidates_opportunity
                ON instrument_candidates(opportunity_id);

                CREATE INDEX IF NOT EXISTS
                idx_instrument_candidates_eligible
                ON instrument_candidates(eligible);

                CREATE INDEX IF NOT EXISTS
                idx_instrument_candidates_score
                ON instrument_candidates(suitability_score);

                CREATE TABLE IF NOT EXISTS position_sizings (
                    sizing_id TEXT PRIMARY KEY,

                    opportunity_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    execution_side TEXT NOT NULL,

                    quantity REAL NOT NULL,

                    gross_exposure_eur REAL NOT NULL,

                    estimated_capital_required_eur REAL NOT NULL,

                    estimated_max_loss_eur REAL,

                    constraints_passed INTEGER NOT NULL,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS
                idx_position_sizings_opportunity
                ON position_sizings(opportunity_id);

                CREATE INDEX IF NOT EXISTS
                idx_position_sizings_instrument
                ON position_sizings(instrument_id);

                CREATE INDEX IF NOT EXISTS
                idx_position_sizings_created
                ON position_sizings(created_at);

                CREATE TABLE IF NOT EXISTS trade_proposals (
                    proposal_id TEXT PRIMARY KEY,

                    opportunity_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,

                    instrument_id TEXT NOT NULL,

                    status TEXT NOT NULL,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS
                idx_trade_proposals_opportunity
                ON trade_proposals(opportunity_id);

                CREATE INDEX IF NOT EXISTS
                idx_trade_proposals_created
                ON trade_proposals(created_at);

                CREATE INDEX IF NOT EXISTS
                idx_trade_proposals_status
                ON trade_proposals(status);

                CREATE TABLE IF NOT EXISTS portfolio_simulations (
                    simulation_id TEXT PRIMARY KEY,

                    snapshot_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    constraints_passed INTEGER NOT NULL,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS
                idx_portfolio_simulations_snapshot
                ON portfolio_simulations(snapshot_id);

                CREATE INDEX IF NOT EXISTS
                idx_portfolio_simulations_proposal
                ON portfolio_simulations(proposal_id);

                CREATE INDEX IF NOT EXISTS
                idx_portfolio_simulations_created
                ON portfolio_simulations(created_at);

                CREATE INDEX IF NOT EXISTS
                idx_portfolio_simulations_constraints
                ON portfolio_simulations(constraints_passed);

                CREATE TABLE IF NOT EXISTS cio_decisions (
                    decision_id TEXT PRIMARY KEY,

                    opportunity_id TEXT,
                    proposal_id TEXT NOT NULL,
                    simulation_id TEXT NOT NULL,
                    snapshot_id TEXT,

                    created_at TEXT NOT NULL,

                    decision TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS
                idx_cio_decisions_opportunity
                ON cio_decisions(opportunity_id);

                CREATE INDEX IF NOT EXISTS
                idx_cio_decisions_proposal
                ON cio_decisions(proposal_id);

                CREATE INDEX IF NOT EXISTS
                idx_cio_decisions_simulation
                ON cio_decisions(simulation_id);

                CREATE INDEX IF NOT EXISTS
                idx_cio_decisions_snapshot
                ON cio_decisions(snapshot_id);

                CREATE INDEX IF NOT EXISTS
                idx_cio_decisions_created
                ON cio_decisions(created_at);

                CREATE INDEX IF NOT EXISTS
                idx_cio_decisions_decision
                ON cio_decisions(decision);

                CREATE INDEX IF NOT EXISTS
                idx_cio_decisions_status
                ON cio_decisions(status);

                CREATE TABLE IF NOT EXISTS execution_plans (
                    execution_plan_id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    simulation_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_execution_plans_opportunity ON execution_plans(opportunity_id);
                CREATE INDEX IF NOT EXISTS idx_execution_plans_proposal ON execution_plans(proposal_id);
                CREATE INDEX IF NOT EXISTS idx_execution_plans_simulation ON execution_plans(simulation_id);
                CREATE INDEX IF NOT EXISTS idx_execution_plans_decision ON execution_plans(decision_id);
                CREATE INDEX IF NOT EXISTS idx_execution_plans_snapshot ON execution_plans(snapshot_id);
                CREATE INDEX IF NOT EXISTS idx_execution_plans_instrument ON execution_plans(instrument_id);
                CREATE INDEX IF NOT EXISTS idx_execution_plans_created ON execution_plans(created_at);
                CREATE INDEX IF NOT EXISTS idx_execution_plans_status ON execution_plans(status);

                CREATE TABLE IF NOT EXISTS operator_confirmations (
                    confirmation_id TEXT PRIMARY KEY,

                    execution_plan_id TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    outcome TEXT NOT NULL,

                    source TEXT NOT NULL,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS
                idx_operator_confirmations_execution_plan
                ON operator_confirmations(execution_plan_id);

                CREATE INDEX IF NOT EXISTS
                idx_operator_confirmations_created
                ON operator_confirmations(created_at);

                CREATE INDEX IF NOT EXISTS
                idx_operator_confirmations_outcome
                ON operator_confirmations(outcome);

                CREATE TABLE IF NOT EXISTS trade_outcomes (
                    outcome_id TEXT PRIMARY KEY,

                    opportunity_id TEXT NOT NULL,
                    execution_plan_id TEXT NOT NULL,
                    confirmation_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,

                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,

                    status TEXT NOT NULL,

                    payload_json TEXT NOT NULL,

                    UNIQUE(execution_plan_id)
                );

                CREATE INDEX IF NOT EXISTS
                idx_trade_outcomes_opportunity
                ON trade_outcomes(opportunity_id);

                CREATE INDEX IF NOT EXISTS
                idx_trade_outcomes_execution_plan
                ON trade_outcomes(execution_plan_id);

                CREATE INDEX IF NOT EXISTS
                idx_trade_outcomes_confirmation
                ON trade_outcomes(confirmation_id);

                CREATE INDEX IF NOT EXISTS
                idx_trade_outcomes_instrument
                ON trade_outcomes(instrument_id);

                CREATE INDEX IF NOT EXISTS
                idx_trade_outcomes_created
                ON trade_outcomes(created_at);

                CREATE INDEX IF NOT EXISTS
                idx_trade_outcomes_updated
                ON trade_outcomes(updated_at);

                CREATE INDEX IF NOT EXISTS
                idx_trade_outcomes_status
                ON trade_outcomes(status);

                CREATE TABLE IF NOT EXISTS fineco_instruments (
                    instrument_id TEXT PRIMARY KEY,

                    underlying TEXT NOT NULL,

                    instrument_type TEXT NOT NULL,

                    trading_mode TEXT,

                    fineco_symbol TEXT,

                    market TEXT,

                    quote_currency TEXT,

                    reference_underlying TEXT,

                    exposure_relationship TEXT,

                    broker_leverage REAL,

                    embedded_leverage REAL,

                    cache_status TEXT NOT NULL,

                    last_confirmed TEXT,

                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_fineco_instruments_underlying
                    ON fineco_instruments(underlying);

                CREATE INDEX IF NOT EXISTS idx_fineco_instruments_type
                    ON fineco_instruments(instrument_type);
                """
            )

            self._migrate_fineco_instruments(
                connection
            )

    def _migrate_fineco_instruments(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """
        Upgrade older Fineco Instrument Cache schemas to the current
        Stage 3 schema without deleting existing records.

        Existing direct instruments remain valid:

            reference_underlying = underlying
            exposure_relationship = DIRECT
            embedded_leverage = 1.0

        broker_leverage is populated when the record is next saved
        through the current FinecoInstrument model.
        """

        rows = connection.execute(
            """
            PRAGMA table_info(fineco_instruments)
            """
        ).fetchall()

        existing_columns = {
            row["name"]
            for row in rows
        }

        required_columns = {
            "trading_mode": "TEXT",
            "fineco_symbol": "TEXT",
            "market": "TEXT",
            "quote_currency": "TEXT",
            "reference_underlying": "TEXT",
            "exposure_relationship": "TEXT",
            "broker_leverage": "REAL",
            "embedded_leverage": "REAL",
        }

        for (
            column_name,
            column_type,
        ) in required_columns.items():

            if column_name in existing_columns:
                continue

            connection.execute(
                f"""
                ALTER TABLE fineco_instruments
                ADD COLUMN {column_name} {column_type}
                """
            )

        # -----------------------------------------------------
        # Semantic cache backfill
        # -----------------------------------------------------

        connection.execute(
            """
            UPDATE fineco_instruments
            SET reference_underlying = underlying
            WHERE reference_underlying IS NULL
               OR TRIM(reference_underlying) = ''
            """
        )

        connection.execute(
            """
            UPDATE fineco_instruments
            SET exposure_relationship = ?
            WHERE exposure_relationship IS NULL
               OR TRIM(exposure_relationship) = ''
            """,
            (
                ExposureRelationship.DIRECT.value,
            ),
        )

        # -----------------------------------------------------
        # Leverage V3 backfill
        #
        # Existing ordinary / margin / CFD records have no
        # leverage embedded in the product payoff itself.
        # -----------------------------------------------------

        connection.execute(
            """
            UPDATE fineco_instruments
            SET embedded_leverage = 1.0
            WHERE embedded_leverage IS NULL
            """
        )

        # -----------------------------------------------------
        # Indexes
        # -----------------------------------------------------

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_fineco_instruments_type
            ON fineco_instruments(instrument_type)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_fineco_instruments_mode
            ON fineco_instruments(trading_mode)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_fineco_instruments_symbol
            ON fineco_instruments(fineco_symbol)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_fineco_instruments_market
            ON fineco_instruments(market)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_fineco_instruments_reference
            ON fineco_instruments(reference_underlying)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_fineco_instruments_relationship
            ON fineco_instruments(exposure_relationship)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_fineco_instruments_broker_leverage
            ON fineco_instruments(broker_leverage)
            """
        )

    # =========================================================
    # Account State
    # =========================================================

    def save_account_state(
        self,
        state: AccountState,
    ) -> None:

        self.initialize()

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO account_states (
                    account_state_id,
                    timestamp,
                    payload_json
                )
                VALUES (?, ?, ?)

                ON CONFLICT(account_state_id)
                DO UPDATE SET
                    timestamp = excluded.timestamp,
                    payload_json = excluded.payload_json
                """,
                (
                    state.account_state_id,
                    state.timestamp.isoformat(),
                    state.model_dump_json(),
                ),
            )

    def get_account_state(
        self,
        account_state_id: str,
    ) -> AccountState | None:

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM account_states
                WHERE account_state_id = ?
                """,
                (
                    account_state_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            AccountState.model_validate_json(
                row["payload_json"]
            )
        )

    def get_latest_account_state(
        self,
    ) -> AccountState | None:

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM account_states
                ORDER BY timestamp DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return (
            AccountState.model_validate_json(
                row["payload_json"]
            )
        )

    # =========================================================
    # Portfolio Snapshot
    # =========================================================

    def save_portfolio_snapshot(
        self,
        snapshot: PortfolioSnapshot,
    ) -> None:

        self.initialize()

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO portfolio_snapshots (
                    snapshot_id,
                    timestamp,
                    source_file_hash,
                    quant_engine_version,
                    account_state_id,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(snapshot_id)
                DO UPDATE SET
                    timestamp = excluded.timestamp,
                    source_file_hash =
                        excluded.source_file_hash,
                    quant_engine_version =
                        excluded.quant_engine_version,
                    account_state_id =
                        excluded.account_state_id,
                    payload_json =
                        excluded.payload_json
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.timestamp.isoformat(),
                    snapshot.source_file_hash,
                    snapshot.quant_engine_version,
                    snapshot.account_state_id,
                    snapshot.model_dump_json(),
                ),
            )

    def get_portfolio_snapshot(
        self,
        snapshot_id: str,
    ) -> PortfolioSnapshot | None:

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_snapshots
                WHERE snapshot_id = ?
                """,
                (
                    snapshot_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            PortfolioSnapshot.model_validate_json(
                row["payload_json"]
            )
        )

    def get_latest_portfolio_snapshot(
        self,
    ) -> PortfolioSnapshot | None:

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_snapshots
                ORDER BY timestamp DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return (
            PortfolioSnapshot.model_validate_json(
                row["payload_json"]
            )
        )

    def find_snapshot_by_file_hash(
        self,
        source_file_hash: str,
    ) -> PortfolioSnapshot | None:

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_snapshots
                WHERE source_file_hash = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (
                    source_file_hash,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            PortfolioSnapshot.model_validate_json(
                row["payload_json"]
            )
        )

    # =========================================================
    # Portfolio Risk State
    # =========================================================

    def save_portfolio_risk_state(
        self,
        record: PortfolioRiskStateRecord,
    ) -> None:
        """
        Persist one canonical quantitative risk state.

        The referenced PortfolioSnapshot must already exist.
        """

        self.initialize()

        snapshot = (
            self.get_portfolio_snapshot(
                record.snapshot_id
            )
        )

        if snapshot is None:

            raise ValueError(
                "PortfolioSnapshot not found: "
                f"{record.snapshot_id}"
            )

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO portfolio_risk_states (
                    risk_state_id,
                    snapshot_id,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?)

                ON CONFLICT(risk_state_id)
                DO UPDATE SET
                    snapshot_id =
                        excluded.snapshot_id,

                    created_at =
                        excluded.created_at,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    record.risk_state_id,
                    record.snapshot_id,
                    record.created_at.isoformat(),
                    record.model_dump_json(),
                ),
            )

    def get_portfolio_risk_state(
        self,
        risk_state_id: str,
    ) -> PortfolioRiskStateRecord | None:
        """
        Retrieve one persisted risk-state record by ID.
        """

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_risk_states
                WHERE risk_state_id = ?
                """,
                (
                    risk_state_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            PortfolioRiskStateRecord.model_validate_json(
                row["payload_json"]
            )
        )

    def list_portfolio_risk_states(
        self,
        snapshot_id: str,
    ) -> list[PortfolioRiskStateRecord]:
        """
        List persisted risk states for one PortfolioSnapshot.

        Most recent record is returned first.
        """

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_risk_states

                WHERE snapshot_id = ?

                ORDER BY
                    created_at DESC,
                    risk_state_id DESC
                """,
                (
                    snapshot_id,
                ),
            ).fetchall()

        return [
            PortfolioRiskStateRecord.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def get_latest_portfolio_risk_state(
        self,
        snapshot_id: str,
    ) -> PortfolioRiskStateRecord | None:
        """
        Return the latest canonical risk state for one snapshot.
        """

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_risk_states

                WHERE snapshot_id = ?

                ORDER BY
                    created_at DESC,
                    risk_state_id DESC

                LIMIT 1
                """,
                (
                    snapshot_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            PortfolioRiskStateRecord.model_validate_json(
                row["payload_json"]
            )
        )

    # =========================================================
    # AI Inference Records
    # =========================================================

    def save_ai_inference(
        self,
        record: AIInferenceRecord,
    ) -> None:
        """Persist one canonical AI inference audit record."""

        self.initialize()

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO ai_inferences (
                    inference_id,
                    timestamp,
                    task,
                    provider,
                    model,
                    sensitivity,
                    reasoning_mode,
                    portfolio_snapshot_id,
                    risk_state_id,
                    validation_status,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(inference_id)
                DO UPDATE SET
                    timestamp = excluded.timestamp,
                    task = excluded.task,
                    provider = excluded.provider,
                    model = excluded.model,
                    sensitivity = excluded.sensitivity,
                    reasoning_mode = excluded.reasoning_mode,
                    portfolio_snapshot_id =
                        excluded.portfolio_snapshot_id,
                    risk_state_id = excluded.risk_state_id,
                    validation_status =
                        excluded.validation_status,
                    payload_json = excluded.payload_json
                """,
                (
                    record.inference_id,
                    record.timestamp.isoformat(),
                    record.task.value,
                    record.provider,
                    record.model,
                    record.sensitivity.value,
                    record.reasoning_mode.value,
                    record.portfolio_snapshot_id,
                    record.risk_state_id,
                    record.validation_status.value,
                    record.model_dump_json(),
                ),
            )

    def get_ai_inference(
        self,
        inference_id: str,
    ) -> AIInferenceRecord | None:
        """Retrieve one persisted AI inference by ID."""

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM ai_inferences
                WHERE inference_id = ?
                """,
                (
                    inference_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            AIInferenceRecord.model_validate_json(
                row["payload_json"]
            )
        )

    def list_ai_inferences(
        self,
        *,
        task: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
    ) -> list[AIInferenceRecord]:
        """List AI inference records, most recent first."""

        self.initialize()

        clauses: list[str] = []
        params: list[str] = []

        filters = (
            ("task", task),
            ("provider", provider),
            ("model", model),
            ("portfolio_snapshot_id", portfolio_snapshot_id),
            ("risk_state_id", risk_state_id),
        )

        for column, value in filters:

            if value is not None:
                clauses.append(
                    f"{column} = ?"
                )
                params.append(
                    value
                )

        where_sql = ""

        if clauses:
            where_sql = (
                "WHERE "
                + " AND ".join(clauses)
            )

        with self._connect() as connection:

            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM ai_inferences

                {where_sql}

                ORDER BY
                    timestamp DESC,
                    inference_id DESC
                """,
                tuple(params),
            ).fetchall()

        return [
            AIInferenceRecord.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def get_latest_ai_inference(
        self,
        *,
        task: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
    ) -> AIInferenceRecord | None:
        """Return the latest AI inference matching optional filters."""

        records = self.list_ai_inferences(
            task=task,
            provider=provider,
            model=model,
            portfolio_snapshot_id=portfolio_snapshot_id,
            risk_state_id=risk_state_id,
        )

        if not records:
            return None

        return records[0]

    # =========================================================
    # Market Scans
    # =========================================================

    def save_market_scan(
        self,
        scan: MarketScan,
    ) -> None:
        """Persist one canonical market scan record."""

        self.initialize()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO market_scans (
                    scan_id,
                    created_at,
                    scanner_type,
                    scanner_version,
                    universe_type,
                    status,
                    portfolio_snapshot_id,
                    risk_state_id,
                    started_at,
                    completed_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(scan_id)
                DO UPDATE SET
                    created_at = excluded.created_at,
                    scanner_type = excluded.scanner_type,
                    scanner_version = excluded.scanner_version,
                    universe_type = excluded.universe_type,
                    status = excluded.status,
                    portfolio_snapshot_id =
                        excluded.portfolio_snapshot_id,
                    risk_state_id = excluded.risk_state_id,
                    started_at = excluded.started_at,
                    completed_at = excluded.completed_at,
                    payload_json = excluded.payload_json
                """,
                (
                    scan.scan_id,
                    scan.created_at.isoformat(),
                    scan.scanner_type.value,
                    scan.scanner_version,
                    scan.universe_type.value,
                    scan.status.value,
                    scan.portfolio_snapshot_id,
                    scan.risk_state_id,
                    scan.started_at.isoformat(),
                    (
                        scan.completed_at.isoformat()
                        if scan.completed_at is not None
                        else None
                    ),
                    scan.model_dump_json(),
                ),
            )

    def get_market_scan(
        self,
        scan_id: str,
    ) -> MarketScan | None:
        """Retrieve one persisted market scan by ID."""

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM market_scans
                WHERE scan_id = ?
                """,
                (scan_id,),
            ).fetchone()

        if row is None:
            return None

        return MarketScan.model_validate_json(
            row["payload_json"]
        )

    def list_market_scans(
        self,
        *,
        scanner_type: str | None = None,
        universe_type: str | None = None,
        status: str | None = None,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
    ) -> list[MarketScan]:
        """List market scans, most recent first, with optional filters."""

        self.initialize()

        clauses: list[str] = []
        params: list[str] = []

        filters = (
            ("scanner_type", scanner_type),
            ("universe_type", universe_type),
            ("status", status),
            ("portfolio_snapshot_id", portfolio_snapshot_id),
            ("risk_state_id", risk_state_id),
        )

        for column, value in filters:
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)

        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM market_scans

                {where_sql}

                ORDER BY
                    created_at DESC,
                    scan_id DESC
                """,
                tuple(params),
            ).fetchall()

        return [
            MarketScan.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def get_latest_market_scan(
        self,
        *,
        scanner_type: str | None = None,
        universe_type: str | None = None,
        status: str | None = None,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
    ) -> MarketScan | None:
        """Return the latest market scan matching optional filters."""

        scans = self.list_market_scans(
            scanner_type=scanner_type,
            universe_type=universe_type,
            status=status,
            portfolio_snapshot_id=portfolio_snapshot_id,
            risk_state_id=risk_state_id,
        )

        return scans[0] if scans else None

    # =========================================================
    # Scan Candidates
    # =========================================================

    def save_scan_candidate(
        self,
        candidate: ScanCandidate,
    ) -> None:
        """
        Persist one scan candidate.

        The parent MarketScan must already exist. This is an
        application-level provenance invariant; no relationship to
        TradeOpportunity is created here.
        """

        self.initialize()

        if self.get_market_scan(candidate.scan_id) is None:
            raise ValueError(
                "MarketScan not found: "
                f"{candidate.scan_id}"
            )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scan_candidates (
                    candidate_id,
                    scan_id,
                    created_at,
                    ticker,
                    origin,
                    action,
                    signal_type,
                    raw_score,
                    scanner_confidence,
                    catalyst_type,
                    portfolio_snapshot_id,
                    risk_state_id,
                    requires_research,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(candidate_id)
                DO UPDATE SET
                    scan_id = excluded.scan_id,
                    created_at = excluded.created_at,
                    ticker = excluded.ticker,
                    origin = excluded.origin,
                    action = excluded.action,
                    signal_type = excluded.signal_type,
                    raw_score = excluded.raw_score,
                    scanner_confidence = excluded.scanner_confidence,
                    catalyst_type = excluded.catalyst_type,
                    portfolio_snapshot_id =
                        excluded.portfolio_snapshot_id,
                    risk_state_id = excluded.risk_state_id,
                    requires_research = excluded.requires_research,
                    payload_json = excluded.payload_json
                """,
                (
                    candidate.candidate_id,
                    candidate.scan_id,
                    candidate.created_at.isoformat(),
                    candidate.ticker.upper(),
                    candidate.origin.value,
                    candidate.action.value,
                    candidate.signal_type.value,
                    candidate.raw_score,
                    candidate.scanner_confidence,
                    (
                        candidate.catalyst_type.value
                        if candidate.catalyst_type is not None
                        else None
                    ),
                    candidate.portfolio_snapshot_id,
                    candidate.risk_state_id,
                    int(candidate.requires_research),
                    candidate.model_dump_json(),
                ),
            )

    def get_scan_candidate(
        self,
        candidate_id: str,
    ) -> ScanCandidate | None:
        """Retrieve one persisted scan candidate by ID."""

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM scan_candidates
                WHERE candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()

        if row is None:
            return None

        return ScanCandidate.model_validate_json(
            row["payload_json"]
        )

    def list_scan_candidates(
        self,
        *,
        scan_id: str | None = None,
        ticker: str | None = None,
        origin: str | None = None,
        action: str | None = None,
        signal_type: str | None = None,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
        requires_research: bool | None = None,
    ) -> list[ScanCandidate]:
        """List scan candidates, most recent first, with optional filters."""

        self.initialize()

        clauses: list[str] = []
        params: list[object] = []

        filters = (
            ("scan_id", scan_id),
            ("origin", origin),
            ("action", action),
            ("signal_type", signal_type),
            ("portfolio_snapshot_id", portfolio_snapshot_id),
            ("risk_state_id", risk_state_id),
        )

        for column, value in filters:
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)

        if ticker is not None:
            clauses.append("UPPER(ticker) = ?")
            params.append(ticker.upper())

        if requires_research is not None:
            clauses.append("requires_research = ?")
            params.append(int(requires_research))

        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM scan_candidates

                {where_sql}

                ORDER BY
                    created_at DESC,
                    candidate_id DESC
                """,
                tuple(params),
            ).fetchall()

        return [
            ScanCandidate.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def list_scan_candidates_for_scan(
        self,
        scan_id: str,
    ) -> list[ScanCandidate]:
        """Return all persisted candidates produced by one scan."""

        return self.list_scan_candidates(
            scan_id=scan_id,
        )

    def get_latest_scan_candidate(
        self,
        *,
        ticker: str | None = None,
        origin: str | None = None,
        action: str | None = None,
        signal_type: str | None = None,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
        requires_research: bool | None = None,
    ) -> ScanCandidate | None:
        """Return the latest scan candidate matching optional filters."""

        candidates = self.list_scan_candidates(
            ticker=ticker,
            origin=origin,
            action=action,
            signal_type=signal_type,
            portfolio_snapshot_id=portfolio_snapshot_id,
            risk_state_id=risk_state_id,
            requires_research=requires_research,
        )

        return candidates[0] if candidates else None


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

    # =========================================================
    # Trade Opportunities
    # =========================================================

    def save_trade_opportunity(
        self,
        opportunity: TradeOpportunity,
    ) -> None:
        """
        Persist one CIO trading opportunity.

        TradeOpportunity is independent from the Fineco Instrument
        Cache. An opportunity may therefore exist even when the broker
        instruments required to implement it have not yet been mapped.
        """

        self.initialize()

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO trade_opportunities (
                    opportunity_id,
                    snapshot_id,
                    created_at,
                    updated_at,
                    ticker,
                    direction,
                    horizon,
                    status,
                    confidence,
                    broker_instruments_available,
                    broker_instrument_count,
                    payload_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )

                ON CONFLICT(opportunity_id)
                DO UPDATE SET
                    snapshot_id =
                        excluded.snapshot_id,

                    created_at =
                        excluded.created_at,

                    updated_at =
                        excluded.updated_at,

                    ticker =
                        excluded.ticker,

                    direction =
                        excluded.direction,

                    horizon =
                        excluded.horizon,

                    status =
                        excluded.status,

                    confidence =
                        excluded.confidence,

                    broker_instruments_available =
                        excluded.broker_instruments_available,

                    broker_instrument_count =
                        excluded.broker_instrument_count,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    opportunity.opportunity_id,
                    opportunity.snapshot_id,
                    opportunity.created_at.isoformat(),

                    (
                        opportunity.updated_at.isoformat()
                        if opportunity.updated_at
                        else None
                    ),

                    opportunity.ticker.upper(),
                    opportunity.direction.value,
                    opportunity.horizon.value,
                    opportunity.status.value,
                    opportunity.confidence,

                    (
                        None
                        if opportunity.broker_instruments_available
                        is None
                        else int(
                            opportunity.broker_instruments_available
                        )
                    ),

                    opportunity.broker_instrument_count,

                    opportunity.model_dump_json(),
                ),
            )

    def get_trade_opportunity(
        self,
        opportunity_id: str,
    ) -> TradeOpportunity | None:
        """
        Retrieve one persisted opportunity by ID.
        """

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM trade_opportunities
                WHERE opportunity_id = ?
                """,
                (
                    opportunity_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            TradeOpportunity.model_validate_json(
                row["payload_json"]
            )
        )

    def list_trade_opportunities(
        self,
        *,
        status: OpportunityStatus | None = None,
    ) -> list[TradeOpportunity]:
        """
        List persisted opportunities.

        When status is supplied, return only opportunities in that
        lifecycle state.
        """

        self.initialize()

        with self._connect() as connection:

            if status is None:

                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM trade_opportunities

                    ORDER BY
                        created_at DESC,
                        opportunity_id
                    """
                ).fetchall()

            else:

                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM trade_opportunities

                    WHERE status = ?

                    ORDER BY
                        created_at DESC,
                        opportunity_id
                    """,
                    (
                        status.value,
                    ),
                ).fetchall()

        return [
            TradeOpportunity.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def find_trade_opportunities(
        self,
        ticker: str,
    ) -> list[TradeOpportunity]:
        """
        Retrieve opportunities for one ticker.

        LONG and SHORT opportunities are deliberately both returned.
        """

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM trade_opportunities

                WHERE UPPER(ticker) = ?

                ORDER BY
                    created_at DESC,
                    opportunity_id
                """,
                (
                    ticker.upper(),
                ),
            ).fetchall()

        return [
            TradeOpportunity.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def delete_trade_opportunity(
        self,
        opportunity_id: str,
    ) -> bool:
        """
        Delete one persisted CIO opportunity.
        """

        self.initialize()

        with self._connect() as connection:

            cursor = connection.execute(
                """
                DELETE FROM trade_opportunities
                WHERE opportunity_id = ?
                """,
                (
                    opportunity_id,
                ),
            )

            return cursor.rowcount > 0

    # =========================================================
    # Instrument Candidates
    # =========================================================

    def replace_instrument_candidates(
        self,
        opportunity_id: str,
        candidates: list[InstrumentCandidate],
    ) -> None:
        """
        Replace the complete Instrument Selector result for one
        opportunity.

        Selection is treated as a snapshot: when the selector is run
        again, the previous ranking for that opportunity is discarded
        and replaced atomically.
        """

        self.initialize()

        for candidate in candidates:

            if (
                candidate.opportunity_id
                != opportunity_id
            ):
                raise ValueError(
                    "All candidates must belong to "
                    "the supplied opportunity_id"
                )

        with self._connect() as connection:

            connection.execute(
                """
                DELETE FROM instrument_candidates
                WHERE opportunity_id = ?
                """,
                (
                    opportunity_id,
                ),
            )

            for candidate in candidates:

                connection.execute(
                    """
                    INSERT INTO instrument_candidates (
                        opportunity_id,
                        instrument_id,
                        eligible,
                        suitability_score,
                        rejection_reason,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.opportunity_id,
                        candidate.instrument_id,
                        int(
                            candidate.eligible
                        ),
                        candidate.suitability_score,
                        candidate.rejection_reason,
                        candidate.model_dump_json(),
                    ),
                )

    def list_instrument_candidates(
        self,
        opportunity_id: str,
    ) -> list[InstrumentCandidate]:
        """
        Retrieve the persisted Instrument Selector ranking.

        Eligible candidates are returned first and ordered by
        descending suitability score.
        """

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM instrument_candidates

                WHERE opportunity_id = ?

                ORDER BY
                    eligible DESC,
                    suitability_score DESC,
                    instrument_id
                """,
                (
                    opportunity_id,
                ),
            ).fetchall()

        return [
            InstrumentCandidate.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def delete_instrument_candidates(
        self,
        opportunity_id: str,
    ) -> int:
        """
        Delete all persisted selector results for one opportunity.
        """

        self.initialize()

        with self._connect() as connection:

            cursor = connection.execute(
                """
                DELETE FROM instrument_candidates
                WHERE opportunity_id = ?
                """,
                (
                    opportunity_id,
                ),
            )

            return cursor.rowcount

    def mark_trade_opportunity_instruments_ranked(
        self,
        opportunity_id: str,
    ) -> TradeOpportunity | None:
        """
        Move an opportunity to INSTRUMENTS_RANKED.

        At least one eligible persisted InstrumentCandidate is required.
        """

        opportunity = (
            self.get_trade_opportunity(
                opportunity_id
            )
        )

        if opportunity is None:
            return None

        candidates = (
            self.list_instrument_candidates(
                opportunity_id
            )
        )

        eligible = [
            candidate
            for candidate in candidates
            if candidate.eligible
        ]

        if not eligible:

            raise ValueError(
                "Cannot mark opportunity INSTRUMENTS_RANKED "
                "without at least one eligible instrument candidate"
            )

        data = (
            opportunity.model_dump()
        )

        data.update(
            {
                "status":
                    OpportunityStatus
                    .INSTRUMENTS_RANKED,

                "updated_at":
                    datetime.now(
                        timezone.utc
                    ),
            }
        )

        updated = (
            TradeOpportunity.model_validate(
                data
            )
        )

        self.save_trade_opportunity(
            updated
        )

        return updated

    # =========================================================
    # Position Sizing
    # =========================================================

    def save_position_sizing(
        self,
        sizing: PositionSizingResult,
    ) -> None:

        self.initialize()

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO position_sizings (
                    sizing_id,
                    opportunity_id,
                    instrument_id,
                    created_at,
                    execution_side,
                    quantity,
                    gross_exposure_eur,
                    estimated_capital_required_eur,
                    estimated_max_loss_eur,
                    constraints_passed,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(sizing_id)
                DO UPDATE SET
                    opportunity_id =
                        excluded.opportunity_id,

                    instrument_id =
                        excluded.instrument_id,

                    created_at =
                        excluded.created_at,

                    execution_side =
                        excluded.execution_side,

                    quantity =
                        excluded.quantity,

                    gross_exposure_eur =
                        excluded.gross_exposure_eur,

                    estimated_capital_required_eur =
                        excluded.estimated_capital_required_eur,

                    estimated_max_loss_eur =
                        excluded.estimated_max_loss_eur,

                    constraints_passed =
                        excluded.constraints_passed,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    sizing.sizing_id,
                    sizing.opportunity_id,
                    sizing.instrument_id,
                    sizing.created_at.isoformat(),
                    sizing.execution_side.value,
                    sizing.quantity,
                    sizing.gross_exposure_eur,
                    sizing.estimated_capital_required_eur,
                    sizing.estimated_max_loss_eur,
                    int(
                        sizing.constraints_passed
                    ),
                    sizing.model_dump_json(),
                ),
            )

    def get_position_sizing(
        self,
        sizing_id: str,
    ) -> PositionSizingResult | None:

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM position_sizings
                WHERE sizing_id = ?
                """,
                (
                    sizing_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            PositionSizingResult
            .model_validate_json(
                row["payload_json"]
            )
        )

    def list_position_sizings(
        self,
        opportunity_id: str,
    ) -> list[PositionSizingResult]:

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM position_sizings

                WHERE opportunity_id = ?

                ORDER BY
                    created_at DESC,
                    sizing_id DESC
                """,
                (
                    opportunity_id,
                ),
            ).fetchall()

        return [
            PositionSizingResult.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def get_latest_position_sizing(
        self,
        opportunity_id: str,
    ) -> PositionSizingResult | None:

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM position_sizings

                WHERE opportunity_id = ?

                ORDER BY
                    created_at DESC,
                    sizing_id DESC

                LIMIT 1
                """,
                (
                    opportunity_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            PositionSizingResult
            .model_validate_json(
                row["payload_json"]
            )
        )

    def get_top_instrument_candidate(
        self,
        opportunity_id: str,
    ) -> InstrumentCandidate | None:
        """
        Return the highest-ranked eligible persisted candidate.

        list_instrument_candidates() already returns eligible candidates
        first and sorts them by descending suitability_score.
        """

        candidates = (
            self.list_instrument_candidates(
                opportunity_id
            )
        )

        for candidate in candidates:

            if candidate.eligible:
                return candidate

        return None

    def mark_trade_opportunity_position_sized(
        self,
        opportunity_id: str,
        sizing_id: str,
    ) -> TradeOpportunity | None:
        """
        Move an opportunity to POSITION_SIZED.

        The sizing must exist, belong to the supplied opportunity,
        and have constraints_passed=True.
        """

        opportunity = (
            self.get_trade_opportunity(
                opportunity_id
            )
        )

        if opportunity is None:
            return None

        sizing = (
            self.get_position_sizing(
                sizing_id
            )
        )

        if sizing is None:

            raise ValueError(
                "PositionSizingResult not found: "
                f"{sizing_id}"
            )

        if (
            sizing.opportunity_id
            != opportunity_id
        ):

            raise ValueError(
                "PositionSizingResult does not belong "
                "to the supplied opportunity"
            )

        if not sizing.constraints_passed:

            raise ValueError(
                "Cannot mark opportunity POSITION_SIZED "
                "when sizing constraints failed"
            )

        data = (
            opportunity.model_dump()
        )

        data.update(
            {
                "status":
                    OpportunityStatus.POSITION_SIZED,

                "updated_at":
                    datetime.now(
                        timezone.utc
                    ),
            }
        )

        updated = (
            TradeOpportunity.model_validate(
                data
            )
        )

        self.save_trade_opportunity(
            updated
        )

        return updated

    # =========================================================
    # Trade Proposals
    # =========================================================

    def save_trade_proposal(
        self,
        proposal: TradeProposal,
    ) -> None:
        """
        Persist one preliminary CIO TradeProposal.

        TradeProposal remains a CIO planning object. It does not imply
        broker execution or operator confirmation.
        """

        self.initialize()

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO trade_proposals (
                    proposal_id,
                    opportunity_id,
                    snapshot_id,
                    created_at,
                    ticker,
                    direction,
                    instrument_id,
                    status,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(proposal_id)
                DO UPDATE SET
                    opportunity_id =
                        excluded.opportunity_id,

                    snapshot_id =
                        excluded.snapshot_id,

                    created_at =
                        excluded.created_at,

                    ticker =
                        excluded.ticker,

                    direction =
                        excluded.direction,

                    instrument_id =
                        excluded.instrument_id,

                    status =
                        excluded.status,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    proposal.proposal_id,
                    proposal.opportunity_id,
                    proposal.snapshot_id,
                    proposal.created_at.isoformat(),
                    proposal.ticker.upper(),
                    proposal.direction.value,
                    proposal.instrument_id,
                    proposal.status.value,
                    proposal.model_dump_json(),
                ),
            )

    def get_trade_proposal(
        self,
        proposal_id: str,
    ) -> TradeProposal | None:
        """
        Retrieve one persisted TradeProposal by ID.
        """

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM trade_proposals
                WHERE proposal_id = ?
                """,
                (
                    proposal_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            TradeProposal.model_validate_json(
                row["payload_json"]
            )
        )

    def list_trade_proposals(
        self,
        opportunity_id: str,
    ) -> list[TradeProposal]:
        """
        List all persisted proposals for one opportunity.

        Most recent proposal is returned first.
        """

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM trade_proposals

                WHERE opportunity_id = ?

                ORDER BY
                    created_at DESC,
                    proposal_id DESC
                """,
                (
                    opportunity_id,
                ),
            ).fetchall()

        return [
            TradeProposal.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def get_latest_trade_proposal(
        self,
        opportunity_id: str,
    ) -> TradeProposal | None:
        """
        Return the most recent persisted proposal for one opportunity.
        """

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM trade_proposals

                WHERE opportunity_id = ?

                ORDER BY
                    created_at DESC,
                    proposal_id DESC

                LIMIT 1
                """,
                (
                    opportunity_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            TradeProposal.model_validate_json(
                row["payload_json"]
            )
        )

    def mark_trade_opportunity_ready_for_proposal(
        self,
        opportunity_id: str,
        proposal_id: str,
    ) -> TradeOpportunity | None:
        """
        Move an opportunity to READY_FOR_PROPOSAL.

        The proposal must:
        - exist;
        - belong to the supplied opportunity;
        - reference the same snapshot as the opportunity.

        The opportunity must already have reached POSITION_SIZED or
        READY_FOR_PROPOSAL.
        """

        opportunity = (
            self.get_trade_opportunity(
                opportunity_id
            )
        )

        if opportunity is None:
            return None

        proposal = (
            self.get_trade_proposal(
                proposal_id
            )
        )

        if proposal is None:

            raise ValueError(
                "TradeProposal not found: "
                f"{proposal_id}"
            )

        if (
            proposal.opportunity_id
            != opportunity_id
        ):

            raise ValueError(
                "TradeProposal does not belong "
                "to the supplied opportunity"
            )

        if (
            proposal.snapshot_id
            != opportunity.snapshot_id
        ):

            raise ValueError(
                "TradeProposal snapshot_id does not match "
                "the opportunity snapshot_id"
            )

        if opportunity.status not in {
            OpportunityStatus.POSITION_SIZED,
            OpportunityStatus.READY_FOR_PROPOSAL,
        }:

            raise ValueError(
                "Opportunity must be POSITION_SIZED before "
                "it can become READY_FOR_PROPOSAL"
            )

        data = (
            opportunity.model_dump()
        )

        data.update(
            {
                "status":
                    OpportunityStatus.READY_FOR_PROPOSAL,

                "updated_at":
                    datetime.now(
                        timezone.utc
                    ),
            }
        )

        updated = (
            TradeOpportunity.model_validate(
                data
            )
        )

        self.save_trade_opportunity(
            updated
        )

        return updated

    # =========================================================
    # Portfolio Simulations
    # =========================================================


    def save_portfolio_fit_assessment(
        self,
        assessment: "PortfolioFitAssessment",
    ) -> None:
        from app.cio.models import PortfolioFitAssessment

        if not isinstance(assessment, PortfolioFitAssessment):
            raise TypeError("assessment must be PortfolioFitAssessment")

        self.initialize()

        opportunity = self.get_trade_opportunity(assessment.opportunity_id)
        if opportunity is None:
            raise ValueError(
                f"TradeOpportunity not found: {assessment.opportunity_id}"
            )

        snapshot = self.get_portfolio_snapshot(assessment.snapshot_id)
        if snapshot is None:
            raise ValueError(
                f"PortfolioSnapshot not found: {assessment.snapshot_id}"
            )

        if opportunity.snapshot_id != assessment.snapshot_id:
            raise ValueError(
                "PortfolioFitAssessment snapshot_id does not match "
                "the TradeOpportunity snapshot_id"
            )

        if opportunity.ticker != assessment.ticker:
            raise ValueError(
                "PortfolioFitAssessment ticker does not match "
                "the TradeOpportunity ticker"
            )

        if opportunity.direction != assessment.direction:
            raise ValueError(
                "PortfolioFitAssessment direction does not match "
                "the TradeOpportunity direction"
            )

        if assessment.risk_state_id is not None:
            risk_state = self.get_portfolio_risk_state(
                assessment.risk_state_id
            )
            if risk_state is None:
                raise ValueError(
                    f"PortfolioRiskState not found: {assessment.risk_state_id}"
                )
            if risk_state.snapshot_id != assessment.snapshot_id:
                raise ValueError(
                    "PortfolioFitAssessment risk_state_id does not "
                    "belong to the assessment snapshot_id"
                )

        if assessment.account_state_id is not None:
            if self.get_account_state(assessment.account_state_id) is None:
                raise ValueError(
                    f"AccountState not found: {assessment.account_state_id}"
                )

        breakdown = assessment.score_breakdown
        scoring_coverage = (
            breakdown.scoring_coverage_pct if breakdown is not None else None
        )
        analytical_coverage = (
            breakdown.analytical_coverage_pct if breakdown is not None else None
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_fit_assessments (
                    assessment_id, opportunity_id, snapshot_id,
                    risk_state_id, account_state_id, created_at,
                    ticker, direction, decision, portfolio_fit_score,
                    scoring_coverage_pct, analytical_coverage_pct,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(assessment_id)
                DO UPDATE SET
                    opportunity_id = excluded.opportunity_id,
                    snapshot_id = excluded.snapshot_id,
                    risk_state_id = excluded.risk_state_id,
                    account_state_id = excluded.account_state_id,
                    created_at = excluded.created_at,
                    ticker = excluded.ticker,
                    direction = excluded.direction,
                    decision = excluded.decision,
                    portfolio_fit_score = excluded.portfolio_fit_score,
                    scoring_coverage_pct = excluded.scoring_coverage_pct,
                    analytical_coverage_pct = excluded.analytical_coverage_pct,
                    payload_json = excluded.payload_json
                """,
                (
                    assessment.assessment_id,
                    assessment.opportunity_id,
                    assessment.snapshot_id,
                    assessment.risk_state_id,
                    assessment.account_state_id,
                    assessment.created_at.isoformat(),
                    assessment.ticker,
                    assessment.direction.value,
                    assessment.decision.value,
                    assessment.portfolio_fit_score,
                    scoring_coverage,
                    analytical_coverage,
                    assessment.model_dump_json(),
                ),
            )

    def get_portfolio_fit_assessment(
        self,
        assessment_id: str,
    ) -> "PortfolioFitAssessment | None":
        from app.cio.models import PortfolioFitAssessment

        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_fit_assessments
                WHERE assessment_id = ?
                """,
                (assessment_id,),
            ).fetchone()

        if row is None:
            return None
        return PortfolioFitAssessment.model_validate_json(row["payload_json"])

    def get_latest_portfolio_fit_assessment(
        self,
        opportunity_id: str,
    ) -> "PortfolioFitAssessment | None":
        from app.cio.models import PortfolioFitAssessment

        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_fit_assessments
                WHERE opportunity_id = ?
                ORDER BY created_at DESC, assessment_id DESC
                LIMIT 1
                """,
                (opportunity_id,),
            ).fetchone()

        if row is None:
            return None
        return PortfolioFitAssessment.model_validate_json(row["payload_json"])

    def list_portfolio_fit_assessments(
        self,
        opportunity_id: str,
    ) -> list["PortfolioFitAssessment"]:
        from app.cio.models import PortfolioFitAssessment

        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_fit_assessments
                WHERE opportunity_id = ?
                ORDER BY created_at DESC, assessment_id DESC
                """,
                (opportunity_id,),
            ).fetchall()

        return [
            PortfolioFitAssessment.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def save_portfolio_simulation(
        self,
        simulation: PortfolioSimulation,
    ) -> None:
        """
        Persist one hypothetical portfolio simulation.

        Referential consistency is checked at application level:

        - TradeProposal must already exist.
        - PortfolioSnapshot must already exist.
        - simulation.snapshot_id must match proposal.snapshot_id.

        PortfolioSimulation is analytical only and never represents
        broker execution.
        """

        self.initialize()

        proposal = (
            self.get_trade_proposal(
                simulation.proposal_id
            )
        )

        if proposal is None:

            raise ValueError(
                "TradeProposal not found: "
                f"{simulation.proposal_id}"
            )

        snapshot = (
            self.get_portfolio_snapshot(
                simulation.snapshot_id
            )
        )

        if snapshot is None:

            raise ValueError(
                "PortfolioSnapshot not found: "
                f"{simulation.snapshot_id}"
            )

        if (
            proposal.snapshot_id
            != simulation.snapshot_id
        ):

            raise ValueError(
                "PortfolioSimulation snapshot_id does not match "
                "the TradeProposal snapshot_id"
            )

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO portfolio_simulations (
                    simulation_id,
                    snapshot_id,
                    proposal_id,
                    created_at,
                    constraints_passed,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(simulation_id)
                DO UPDATE SET
                    snapshot_id =
                        excluded.snapshot_id,

                    proposal_id =
                        excluded.proposal_id,

                    created_at =
                        excluded.created_at,

                    constraints_passed =
                        excluded.constraints_passed,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    simulation.simulation_id,
                    simulation.snapshot_id,
                    simulation.proposal_id,
                    simulation.created_at.isoformat(),
                    int(
                        simulation.constraints_passed
                    ),
                    simulation.model_dump_json(),
                ),
            )

    def get_portfolio_simulation(
        self,
        simulation_id: str,
    ) -> PortfolioSimulation | None:
        """
        Retrieve one persisted PortfolioSimulation by ID.
        """

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_simulations
                WHERE simulation_id = ?
                """,
                (
                    simulation_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            PortfolioSimulation.model_validate_json(
                row["payload_json"]
            )
        )

    def list_portfolio_simulations(
        self,
        proposal_id: str,
    ) -> list[PortfolioSimulation]:
        """
        List all persisted simulations for one TradeProposal.

        Most recent simulation is returned first.
        """

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_simulations

                WHERE proposal_id = ?

                ORDER BY
                    created_at DESC,
                    simulation_id DESC
                """,
                (
                    proposal_id,
                ),
            ).fetchall()

        return [
            PortfolioSimulation.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def get_latest_portfolio_simulation(
        self,
        proposal_id: str,
    ) -> PortfolioSimulation | None:
        """
        Return the most recent simulation for one TradeProposal.
        """

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM portfolio_simulations

                WHERE proposal_id = ?

                ORDER BY
                    created_at DESC,
                    simulation_id DESC

                LIMIT 1
                """,
                (
                    proposal_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            PortfolioSimulation.model_validate_json(
                row["payload_json"]
            )
        )

    # =========================================================
    # CIO Decisions
    # =========================================================

    def save_cio_decision(
        self,
        decision: CioDecision,
    ) -> None:
        """
        Persist one preliminary or operator-reviewed CIO decision.

        Application-level referential checks guarantee that:

        - TradeProposal exists.
        - PortfolioSimulation exists.
        - simulation.proposal_id matches decision.proposal_id.
        - decision opportunity/snapshot provenance, when supplied,
          matches the persisted proposal.

        This remains a CIO governance record. It is not a broker order
        and does not imply execution.
        """

        self.initialize()

        proposal = self.get_trade_proposal(
            decision.proposal_id
        )

        if proposal is None:
            raise ValueError(
                "TradeProposal not found: "
                f"{decision.proposal_id}"
            )

        simulation = self.get_portfolio_simulation(
            decision.simulation_id
        )

        if simulation is None:
            raise ValueError(
                "PortfolioSimulation not found: "
                f"{decision.simulation_id}"
            )

        if simulation.proposal_id != decision.proposal_id:
            raise ValueError(
                "CioDecision simulation_id does not belong "
                "to the supplied proposal_id"
            )

        if simulation.snapshot_id != proposal.snapshot_id:
            raise ValueError(
                "Persisted PortfolioSimulation snapshot_id does not "
                "match the TradeProposal snapshot_id"
            )

        if (
            decision.opportunity_id is not None
            and decision.opportunity_id != proposal.opportunity_id
        ):
            raise ValueError(
                "CioDecision opportunity_id does not match "
                "the TradeProposal opportunity_id"
            )

        if (
            decision.snapshot_id is not None
            and decision.snapshot_id != proposal.snapshot_id
        ):
            raise ValueError(
                "CioDecision snapshot_id does not match "
                "the TradeProposal snapshot_id"
            )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cio_decisions (
                    decision_id,
                    opportunity_id,
                    proposal_id,
                    simulation_id,
                    snapshot_id,
                    created_at,
                    decision,
                    confidence,
                    status,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(decision_id)
                DO UPDATE SET
                    opportunity_id =
                        excluded.opportunity_id,

                    proposal_id =
                        excluded.proposal_id,

                    simulation_id =
                        excluded.simulation_id,

                    snapshot_id =
                        excluded.snapshot_id,

                    created_at =
                        excluded.created_at,

                    decision =
                        excluded.decision,

                    confidence =
                        excluded.confidence,

                    status =
                        excluded.status,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    decision.decision_id,
                    decision.opportunity_id,
                    decision.proposal_id,
                    decision.simulation_id,
                    decision.snapshot_id,
                    decision.created_at.isoformat(),
                    decision.decision.value,
                    decision.confidence,
                    decision.status.value,
                    decision.model_dump_json(),
                ),
            )

    def get_cio_decision(
        self,
        decision_id: str,
    ) -> CioDecision | None:
        """
        Retrieve one persisted CIO decision by ID.
        """

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM cio_decisions
                WHERE decision_id = ?
                """,
                (
                    decision_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return CioDecision.model_validate_json(
            row["payload_json"]
        )

    def list_cio_decisions(
        self,
        opportunity_id: str,
    ) -> list[CioDecision]:
        """
        List all persisted CIO decisions for one opportunity.

        Most recent decision is returned first.
        """

        self.initialize()

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM cio_decisions

                WHERE opportunity_id = ?

                ORDER BY
                    created_at DESC,
                    decision_id DESC
                """,
                (
                    opportunity_id,
                ),
            ).fetchall()

        return [
            CioDecision.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def list_cio_decisions_for_proposal(
        self,
        proposal_id: str,
    ) -> list[CioDecision]:
        """
        List all persisted CIO decisions for one TradeProposal.

        Most recent decision is returned first.
        """

        self.initialize()

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM cio_decisions

                WHERE proposal_id = ?

                ORDER BY
                    created_at DESC,
                    decision_id DESC
                """,
                (
                    proposal_id,
                ),
            ).fetchall()

        return [
            CioDecision.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def get_latest_cio_decision(
        self,
        opportunity_id: str,
    ) -> CioDecision | None:
        """
        Return the latest persisted CIO decision for one opportunity.
        """

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM cio_decisions

                WHERE opportunity_id = ?

                ORDER BY
                    created_at DESC,
                    decision_id DESC

                LIMIT 1
                """,
                (
                    opportunity_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return CioDecision.model_validate_json(
            row["payload_json"]
        )

    def get_latest_cio_decision_for_proposal(
        self,
        proposal_id: str,
    ) -> CioDecision | None:
        """
        Return the latest persisted CIO decision for one TradeProposal.
        """

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM cio_decisions

                WHERE proposal_id = ?

                ORDER BY
                    created_at DESC,
                    decision_id DESC

                LIMIT 1
                """,
                (
                    proposal_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return CioDecision.model_validate_json(
            row["payload_json"]
        )

    # =========================================================
    # Execution Plans
    # =========================================================

    def save_execution_plan(self, plan: ExecutionPlan) -> None:
        self.initialize()
        opportunity = self.get_trade_opportunity(plan.opportunity_id)
        proposal = self.get_trade_proposal(plan.proposal_id)
        simulation = self.get_portfolio_simulation(plan.simulation_id)
        decision = self.get_cio_decision(plan.decision_id)
        snapshot = self.get_portfolio_snapshot(plan.snapshot_id)
        instrument = self.get_fineco_instrument(plan.instrument_id)
        if opportunity is None: raise ValueError(f"TradeOpportunity not found: {plan.opportunity_id}")
        if proposal is None: raise ValueError(f"TradeProposal not found: {plan.proposal_id}")
        if simulation is None: raise ValueError(f"PortfolioSimulation not found: {plan.simulation_id}")
        if decision is None: raise ValueError(f"CioDecision not found: {plan.decision_id}")
        if snapshot is None: raise ValueError(f"PortfolioSnapshot not found: {plan.snapshot_id}")
        if instrument is None: raise ValueError(f"FinecoInstrument not found: {plan.instrument_id}")
        if proposal.opportunity_id != plan.opportunity_id: raise ValueError("ExecutionPlan opportunity_id does not match the TradeProposal opportunity_id")
        if proposal.snapshot_id != plan.snapshot_id: raise ValueError("ExecutionPlan snapshot_id does not match the TradeProposal snapshot_id")
        if proposal.instrument_id != plan.instrument_id: raise ValueError("ExecutionPlan instrument_id does not match the TradeProposal instrument_id")
        if simulation.proposal_id != plan.proposal_id: raise ValueError("ExecutionPlan simulation_id does not belong to the supplied proposal_id")
        if simulation.snapshot_id != plan.snapshot_id: raise ValueError("ExecutionPlan snapshot_id does not match the PortfolioSimulation snapshot_id")
        if decision.proposal_id != plan.proposal_id: raise ValueError("ExecutionPlan decision_id does not belong to the supplied proposal_id")
        if decision.simulation_id != plan.simulation_id: raise ValueError("ExecutionPlan decision_id does not belong to the supplied simulation_id")
        if decision.opportunity_id is not None and decision.opportunity_id != plan.opportunity_id: raise ValueError("ExecutionPlan opportunity_id does not match the CioDecision opportunity_id")
        if decision.snapshot_id is not None and decision.snapshot_id != plan.snapshot_id: raise ValueError("ExecutionPlan snapshot_id does not match the CioDecision snapshot_id")
        with self._connect() as connection:
            connection.execute("""INSERT INTO execution_plans (execution_plan_id, opportunity_id, proposal_id, simulation_id, decision_id, snapshot_id, created_at, instrument_id, status, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(execution_plan_id) DO UPDATE SET opportunity_id=excluded.opportunity_id, proposal_id=excluded.proposal_id, simulation_id=excluded.simulation_id, decision_id=excluded.decision_id, snapshot_id=excluded.snapshot_id, created_at=excluded.created_at, instrument_id=excluded.instrument_id, status=excluded.status, payload_json=excluded.payload_json""", (plan.execution_plan_id, plan.opportunity_id, plan.proposal_id, plan.simulation_id, plan.decision_id, plan.snapshot_id, plan.created_at.isoformat(), plan.instrument_id, plan.status.value, plan.model_dump_json()))

    def get_execution_plan(self, execution_plan_id: str) -> ExecutionPlan | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM execution_plans WHERE execution_plan_id = ?", (execution_plan_id,)).fetchone()
        return None if row is None else ExecutionPlan.model_validate_json(row["payload_json"])

    def list_execution_plans(self, opportunity_id: str) -> list[ExecutionPlan]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM execution_plans WHERE opportunity_id = ? ORDER BY created_at DESC, execution_plan_id DESC", (opportunity_id,)).fetchall()
        return [ExecutionPlan.model_validate_json(row["payload_json"]) for row in rows]

    def list_execution_plans_for_proposal(self, proposal_id: str) -> list[ExecutionPlan]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM execution_plans WHERE proposal_id = ? ORDER BY created_at DESC, execution_plan_id DESC", (proposal_id,)).fetchall()
        return [ExecutionPlan.model_validate_json(row["payload_json"]) for row in rows]

    def get_latest_execution_plan(self, opportunity_id: str) -> ExecutionPlan | None:
        plans = self.list_execution_plans(opportunity_id)
        return plans[0] if plans else None

    def get_latest_execution_plan_for_proposal(self, proposal_id: str) -> ExecutionPlan | None:
        plans = self.list_execution_plans_for_proposal(proposal_id)
        return plans[0] if plans else None

    # =========================================================
    # Operator Confirmations
    # =========================================================

    def save_operator_confirmation(
        self,
        confirmation: OperatorConfirmation,
    ) -> None:
        """
        Persist one explicit human operator confirmation.

        The referenced ExecutionPlan must already exist.

        This method deliberately persists the confirmation record only.
        It does NOT mutate the ExecutionPlan lifecycle. That transition
        belongs to OperatorConfirmationService so the write-side
        workflow remains explicit and auditable.
        """

        self.initialize()

        plan = self.get_execution_plan(
            confirmation.execution_plan_id
        )

        if plan is None:
            raise ValueError(
                "ExecutionPlan not found: "
                f"{confirmation.execution_plan_id}"
            )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO operator_confirmations (
                    confirmation_id,
                    execution_plan_id,
                    created_at,
                    outcome,
                    source,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(confirmation_id)
                DO UPDATE SET
                    execution_plan_id =
                        excluded.execution_plan_id,

                    created_at =
                        excluded.created_at,

                    outcome =
                        excluded.outcome,

                    source =
                        excluded.source,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    confirmation.confirmation_id,
                    confirmation.execution_plan_id,
                    confirmation.created_at.isoformat(),
                    confirmation.outcome.value,
                    confirmation.source.value,
                    confirmation.model_dump_json(),
                ),
            )

    def get_operator_confirmation(
        self,
        confirmation_id: str,
    ) -> OperatorConfirmation | None:
        """
        Retrieve one persisted OperatorConfirmation by ID.
        """

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM operator_confirmations
                WHERE confirmation_id = ?
                """,
                (
                    confirmation_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return OperatorConfirmation.model_validate_json(
            row["payload_json"]
        )

    def list_operator_confirmations_for_execution_plan(
        self,
        execution_plan_id: str,
    ) -> list[OperatorConfirmation]:
        """
        List all confirmations for one ExecutionPlan.

        Most recent confirmation is returned first.
        """

        self.initialize()

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM operator_confirmations

                WHERE execution_plan_id = ?

                ORDER BY
                    created_at DESC,
                    confirmation_id DESC
                """,
                (
                    execution_plan_id,
                ),
            ).fetchall()

        return [
            OperatorConfirmation.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def get_latest_operator_confirmation_for_execution_plan(
        self,
        execution_plan_id: str,
    ) -> OperatorConfirmation | None:
        """
        Return the latest confirmation for one ExecutionPlan.
        """

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM operator_confirmations

                WHERE execution_plan_id = ?

                ORDER BY
                    created_at DESC,
                    confirmation_id DESC

                LIMIT 1
                """,
                (
                    execution_plan_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return OperatorConfirmation.model_validate_json(
            row["payload_json"]
        )

    # =========================================================
    # Trade Outcomes
    # =========================================================

    def save_trade_outcome(
        self,
        outcome: TradeOutcome,
    ) -> None:
        """
        Persist one canonical TradeOutcome.

        Referential integrity
        ---------------------
        The referenced ExecutionPlan and OperatorConfirmation must
        already exist and describe the exact same executed trade.

        One ExecutionPlan may have at most one TradeOutcome. The same
        TradeOutcome may later be updated from OPEN to CLOSED while
        preserving its outcome_id and provenance.

        This method persists factual outcome state only. It does not
        calculate P/L, close positions or infer broker activity.
        """

        self.initialize()

        plan = self.get_execution_plan(
            outcome.execution_plan_id
        )

        if plan is None:
            raise ValueError(
                "ExecutionPlan not found: "
                f"{outcome.execution_plan_id}"
            )

        confirmation = self.get_operator_confirmation(
            outcome.confirmation_id
        )

        if confirmation is None:
            raise ValueError(
                "OperatorConfirmation not found: "
                f"{outcome.confirmation_id}"
            )

        if (
            confirmation.execution_plan_id
            != outcome.execution_plan_id
        ):
            raise ValueError(
                "TradeOutcome confirmation_id does not belong "
                "to the supplied execution_plan_id"
            )

        # -----------------------------------------------------
        # Exact provenance inherited from ExecutionPlan.
        # -----------------------------------------------------

        provenance_pairs = [
            (
                "opportunity_id",
                outcome.opportunity_id,
                plan.opportunity_id,
            ),
            (
                "proposal_id",
                outcome.proposal_id,
                plan.proposal_id,
            ),
            (
                "simulation_id",
                outcome.simulation_id,
                plan.simulation_id,
            ),
            (
                "decision_id",
                outcome.decision_id,
                plan.decision_id,
            ),
            (
                "snapshot_id",
                outcome.snapshot_id,
                plan.snapshot_id,
            ),
            (
                "instrument_id",
                outcome.instrument_id,
                plan.instrument_id,
            ),
            (
                "direction",
                outcome.direction,
                plan.direction,
            ),
            (
                "execution_side",
                outcome.execution_side,
                plan.execution_side,
            ),
            (
                "currency",
                outcome.currency,
                plan.currency,
            ),
        ]

        for (
            field_name,
            outcome_value,
            plan_value,
        ) in provenance_pairs:

            if outcome_value != plan_value:
                raise ValueError(
                    f"TradeOutcome {field_name} does not match "
                    "ExecutionPlan"
                )

        # -----------------------------------------------------
        # Actual entry facts inherited from confirmation.
        # -----------------------------------------------------

        if (
            outcome.entry_datetime
            != confirmation.created_at
        ):
            raise ValueError(
                "TradeOutcome entry_datetime does not match "
                "OperatorConfirmation created_at"
            )

        if (
            outcome.entry_quantity
            != confirmation.executed_quantity
        ):
            raise ValueError(
                "TradeOutcome entry_quantity does not match "
                "OperatorConfirmation executed_quantity"
            )

        if (
            outcome.entry_price
            != confirmation.executed_price
        ):
            raise ValueError(
                "TradeOutcome entry_price does not match "
                "OperatorConfirmation executed_price"
            )

        if (
            outcome.entry_commission_eur
            != confirmation.commission_eur
        ):
            raise ValueError(
                "TradeOutcome entry_commission_eur does not match "
                "OperatorConfirmation commission_eur"
            )

        # -----------------------------------------------------
        # One canonical outcome per ExecutionPlan.
        # -----------------------------------------------------

        existing = (
            self.get_trade_outcome_for_execution_plan(
                outcome.execution_plan_id
            )
        )

        if (
            existing is not None
            and existing.outcome_id
            != outcome.outcome_id
        ):
            raise ValueError(
                "ExecutionPlan already has a different TradeOutcome: "
                f"{existing.outcome_id}"
            )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_outcomes (
                    outcome_id,
                    opportunity_id,
                    execution_plan_id,
                    confirmation_id,
                    instrument_id,
                    created_at,
                    updated_at,
                    status,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(outcome_id)
                DO UPDATE SET
                    opportunity_id =
                        excluded.opportunity_id,

                    execution_plan_id =
                        excluded.execution_plan_id,

                    confirmation_id =
                        excluded.confirmation_id,

                    instrument_id =
                        excluded.instrument_id,

                    created_at =
                        excluded.created_at,

                    updated_at =
                        excluded.updated_at,

                    status =
                        excluded.status,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    outcome.outcome_id,
                    outcome.opportunity_id,
                    outcome.execution_plan_id,
                    outcome.confirmation_id,
                    outcome.instrument_id,
                    outcome.created_at.isoformat(),
                    outcome.updated_at.isoformat(),
                    outcome.status.value,
                    outcome.model_dump_json(),
                ),
            )

    def get_trade_outcome(
        self,
        outcome_id: str,
    ) -> TradeOutcome | None:
        """
        Retrieve one persisted TradeOutcome by ID.
        """

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM trade_outcomes
                WHERE outcome_id = ?
                """,
                (
                    outcome_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return TradeOutcome.model_validate_json(
            row["payload_json"]
        )

    def get_trade_outcome_for_execution_plan(
        self,
        execution_plan_id: str,
    ) -> TradeOutcome | None:
        """
        Return the canonical TradeOutcome for one ExecutionPlan.
        """

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM trade_outcomes
                WHERE execution_plan_id = ?
                LIMIT 1
                """,
                (
                    execution_plan_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return TradeOutcome.model_validate_json(
            row["payload_json"]
        )

    def list_trade_outcomes(
        self,
        opportunity_id: str | None = None,
    ) -> list[TradeOutcome]:
        """
        List persisted TradeOutcomes.

        When opportunity_id is supplied, only outcomes belonging to
        that opportunity are returned.

        Most recently updated outcome is returned first.
        """

        self.initialize()

        with self._connect() as connection:

            if opportunity_id is None:

                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM trade_outcomes

                    ORDER BY
                        updated_at DESC,
                        outcome_id DESC
                    """
                ).fetchall()

            else:

                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM trade_outcomes

                    WHERE opportunity_id = ?

                    ORDER BY
                        updated_at DESC,
                        outcome_id DESC
                    """,
                    (
                        opportunity_id,
                    ),
                ).fetchall()

        return [
            TradeOutcome.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def get_latest_trade_outcome(
        self,
        opportunity_id: str,
    ) -> TradeOutcome | None:
        """
        Return the most recently updated TradeOutcome for one opportunity.
        """

        self.initialize()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM trade_outcomes

                WHERE opportunity_id = ?

                ORDER BY
                    updated_at DESC,
                    outcome_id DESC

                LIMIT 1
                """,
                (
                    opportunity_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return TradeOutcome.model_validate_json(
            row["payload_json"]
        )

    # =========================================================
    # Opportunity / Fineco cache integration
    # =========================================================

    def refresh_trade_opportunity_broker_state(
        self,
        opportunity_id: str,
    ) -> TradeOpportunity | None:
        """
        Refresh the Fineco Instrument Cache state for one opportunity.

        This implements the Stage 3.1 bridge:

            CIO discovers opportunity
                    ↓
            Fineco cache lookup
                    ↓
            no instruments
                    ↓
            WAITING_FOR_BROKER_INSTRUMENTS

        and later:

            operator bulk-imports Fineco screenshots
                    ↓
            refresh
                    ↓
            READY_FOR_INSTRUMENT_SELECTION

        The method does NOT perform instrument selection.
        It only determines whether broker instruments are known.

        REJECTED, EXPIRED and READY_FOR_PROPOSAL opportunities are not
        regressed to an earlier workflow state.
        """

        opportunity = self.get_trade_opportunity(
            opportunity_id
        )

        if opportunity is None:
            return None

        # If the workflow explicitly does not require broker
        # instruments, leave the opportunity unchanged.
        if not opportunity.broker_instruments_required:
            return opportunity

        instruments = self.find_fineco_instruments(
            opportunity.ticker
        )

        instrument_count = len(
            instruments
        )

        instruments_available = (
            instrument_count > 0
        )

        terminal_or_advanced_states = {
            OpportunityStatus.REJECTED,
            OpportunityStatus.EXPIRED,
            OpportunityStatus.INSTRUMENTS_RANKED,
            OpportunityStatus.POSITION_SIZED,
            OpportunityStatus.READY_FOR_PROPOSAL,
        }

        if (
            opportunity.status
            in terminal_or_advanced_states
        ):
            new_status = (
                opportunity.status
            )

        elif instruments_available:

            new_status = (
                OpportunityStatus
                .READY_FOR_INSTRUMENT_SELECTION
            )

        else:

            new_status = (
                OpportunityStatus
                .WAITING_FOR_BROKER_INSTRUMENTS
            )

        data = (
            opportunity.model_dump()
        )

        data.update(
            {
                "updated_at":
                    datetime.now(
                        timezone.utc
                    ),

                "broker_instruments_available":
                    instruments_available,

                "broker_instrument_count":
                    instrument_count,

                "status":
                    new_status,
            }
        )

        updated = (
            TradeOpportunity.model_validate(
                data
            )
        )

        self.save_trade_opportunity(
            updated
        )

        return updated

    def refresh_waiting_trade_opportunities(
        self,
    ) -> list[TradeOpportunity]:
        """
        Refresh all opportunities currently waiting for Fineco
        instrument information.

        This is intended to be called after a Fineco bulk import.

        Example:

            bulk import AVGO screenshots
                    ↓
            refresh waiting opportunities
                    ↓
            AVGO moves automatically from
            WAITING_FOR_BROKER_INSTRUMENTS
            to READY_FOR_INSTRUMENT_SELECTION
        """

        waiting = (
            self.list_trade_opportunities(
                status=(
                    OpportunityStatus
                    .WAITING_FOR_BROKER_INSTRUMENTS
                )
            )
        )

        refreshed: list[
            TradeOpportunity
        ] = []

        for opportunity in waiting:

            updated = (
                self.refresh_trade_opportunity_broker_state(
                    opportunity.opportunity_id
                )
            )

            if updated is not None:
                refreshed.append(
                    updated
                )

        return refreshed

    # =========================================================
    # Fineco Instrument Cache
    # =========================================================

    def save_fineco_instrument(
        self,
        instrument: FinecoInstrument,
    ) -> None:

        self.initialize()

        reference_underlying = (
            instrument.reference_underlying
            or instrument.underlying
        ).upper()

        with self._connect() as connection:

            connection.execute(
                """
                INSERT INTO fineco_instruments (
                    instrument_id,
                    underlying,
                    instrument_type,
                    trading_mode,
                    fineco_symbol,
                    market,
                    quote_currency,
                    reference_underlying,
                    exposure_relationship,
                    broker_leverage,
                    embedded_leverage,
                    cache_status,
                    last_confirmed,
                    payload_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )

                ON CONFLICT(instrument_id)
                DO UPDATE SET

                    underlying =
                        excluded.underlying,

                    instrument_type =
                        excluded.instrument_type,

                    trading_mode =
                        excluded.trading_mode,

                    fineco_symbol =
                        excluded.fineco_symbol,

                    market =
                        excluded.market,

                    quote_currency =
                        excluded.quote_currency,

                    reference_underlying =
                        excluded.reference_underlying,

                    exposure_relationship =
                        excluded.exposure_relationship,

                    broker_leverage =
                        excluded.broker_leverage,

                    embedded_leverage =
                        excluded.embedded_leverage,

                    cache_status =
                        excluded.cache_status,

                    last_confirmed =
                        excluded.last_confirmed,

                    payload_json =
                        excluded.payload_json
                """,
                (
                    instrument.instrument_id,

                    instrument.underlying.upper(),

                    instrument.instrument_type.value,

                    instrument.trading_mode.value,

                    instrument.fineco_symbol,

                    instrument.market,

                    instrument.quote_currency.value,

                    reference_underlying,

                    instrument.exposure_relationship.value,

                    instrument.broker_leverage,

                    instrument.embedded_leverage,

                    instrument.cache_status.value,

                    (
                        instrument.last_confirmed.isoformat()
                        if instrument.last_confirmed
                        else None
                    ),

                    instrument.model_dump_json(),
                ),
            )

    def get_fineco_instrument(
        self,
        instrument_id: str,
    ) -> FinecoInstrument | None:

        self.initialize()

        with self._connect() as connection:

            row = connection.execute(
                """
                SELECT payload_json
                FROM fineco_instruments
                WHERE instrument_id = ?
                """,
                (
                    instrument_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return (
            FinecoInstrument.model_validate_json(
                row["payload_json"]
            )
        )

    def find_fineco_instruments(
        self,
        underlying: str,
    ) -> list[FinecoInstrument]:

        self.initialize()

        ticker = underlying.upper()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM fineco_instruments

                WHERE underlying = ?
                   OR reference_underlying = ?

                ORDER BY
                    exposure_relationship,
                    instrument_type,
                    trading_mode,
                    instrument_id
                """,
                (
                    ticker,
                    ticker,
                ),
            ).fetchall()

        return [
            FinecoInstrument.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def list_fineco_instruments(
        self,
    ) -> list[FinecoInstrument]:

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM fineco_instruments

                ORDER BY
                    reference_underlying,
                    exposure_relationship,
                    instrument_type,
                    trading_mode,
                    instrument_id
                """
            ).fetchall()

        return [
            FinecoInstrument.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    # =========================================================
    # Instrument Selector support
    # =========================================================

    def find_instruments_by_mode(
        self,
        underlying: str,
        trading_mode: str,
    ) -> list[FinecoInstrument]:

        self.initialize()

        ticker = underlying.upper()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM fineco_instruments

                WHERE (
                    underlying = ?
                    OR reference_underlying = ?
                )
                AND trading_mode = ?

                ORDER BY
                    exposure_relationship,
                    instrument_type,
                    instrument_id
                """,
                (
                    ticker,
                    ticker,
                    trading_mode.upper(),
                ),
            ).fetchall()

        return [
            FinecoInstrument.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def find_instruments_by_symbol(
        self,
        fineco_symbol: str,
    ) -> list[FinecoInstrument]:

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM fineco_instruments

                WHERE UPPER(fineco_symbol) = ?

                ORDER BY
                    exposure_relationship,
                    trading_mode,
                    instrument_id
                """,
                (
                    fineco_symbol.upper(),
                ),
            ).fetchall()

        return [
            FinecoInstrument.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def find_instruments_by_relationship(
        self,
        reference_underlying: str,
        relationship: str,
    ) -> list[FinecoInstrument]:
        """
        Retrieve instruments implementing a specific economic
        relationship to an underlying.

        Example:

            reference_underlying = GOOGL
            relationship = INVERSE
        """

        self.initialize()

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT payload_json
                FROM fineco_instruments

                WHERE reference_underlying = ?
                  AND exposure_relationship = ?

                ORDER BY
                    instrument_type,
                    trading_mode,
                    instrument_id
                """,
                (
                    reference_underlying.upper(),
                    relationship.upper(),
                ),
            ).fetchall()

        return [
            FinecoInstrument.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    # =========================================================
    # Delete
    # =========================================================

    def delete_fineco_instrument(
        self,
        instrument_id: str,
    ) -> bool:
        """
        Delete one Fineco instrument from the local cache.

        Returns True when an instrument was actually deleted.
        Returns False when the instrument_id did not exist.
        """

        self.initialize()

        with self._connect() as connection:

            cursor = connection.execute(
                """
                DELETE FROM fineco_instruments
                WHERE instrument_id = ?
                """,
                (
                    instrument_id,
                ),
            )

            return cursor.rowcount > 0

    # =========================================================
    # SQLite connection
    # =========================================================

    def _connect(
        self,
    ) -> sqlite3.Connection:

        connection = sqlite3.connect(
            self.db_path
        )

        connection.row_factory = (
            sqlite3.Row
        )

        connection.execute(
            "PRAGMA foreign_keys=ON"
        )

        return connection