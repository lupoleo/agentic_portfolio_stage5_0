"""Stage 5 dry run (S5.0-7 part B): IBKR replaces the Fineco workbook, Yahoo and the Fineco instrument cache."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.analysis.marginal_risk import PortfolioMarginalRiskEngine
from app.analysis.portfolio import BENCHMARK_SYMBOL, RISK_MAX_SESSIONS, RISK_MIN_COMMON_OBSERVATIONS
from app.ai.ibkr_market import IbkrHistoryLoader
from app.broker.cio_adapter import account_state_from_version
from app.broker.gateway import ContractInfo, RecordedGatewaySession
from app.broker.sync_store import SyncStore
from app.cio.instrument_selector import InstrumentSelector
from app.cio.models import Direction, InstrumentType, TradingHorizon
from app.cio.storage import Stage3Store
from app.e2e.stage5_dry_run import (
    IbkrMarginalRiskInputProvider,
    NlvRiskPolicy,
    Stage5DryRunError,
    average_true_range,
    default_exposure_eur,
    fx_to_eur,
    ibkr_instrument,
    market_inputs,
)
from app.e2e.stage5_portfolio import RISK_HISTORY_DURATION, directory_from_version, positions_from_version
from tests.test_stage5_first_e2e import write_inputs
from tools import s5_dry_run, s5_first_e2e

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def run_part_a(tmp_path, capsys):
    gateway, account, fixture = write_inputs(tmp_path)
    code = s5_first_e2e.main([
        "--gateway-recording", str(gateway), "--account-recording", str(account), "--fixture", str(fixture),
        "--db", str(tmp_path / "cio.db"), "--sync-db", str(tmp_path / "sync.db"),
        "--output-directory", str(tmp_path / "out"), "--cache-only", "--no-scanner",
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    (report_path,) = (tmp_path / "out").glob("stage5_first_e2e_*.json")
    return gateway, json.loads(report_path.read_text(encoding="utf-8")), out


def test_pf1_limits_follow_the_live_nlv():
    constraints = NlvRiskPolicy().constraints(10_000)
    assert constraints.max_trade_loss_eur == 150.0
    assert constraints.max_var_95_1d_eur == 300.0 and constraints.min_cash_reserve_eur == 500.0
    assert constraints.max_position_weight_pct == 20.0 and constraints.max_portfolio_gross_exposure_pct == 150.0
    assert constraints.max_cio_deployable_pct == 25.0
    assert default_exposure_eur(10_000, NlvRiskPolicy()) == 1000.0
    with pytest.raises(Stage5DryRunError):
        NlvRiskPolicy().constraints(0)


def test_part_a_persists_an_account_state_with_pf1_limits(tmp_path, capsys):
    _, report, out = run_part_a(tmp_path, capsys)
    assert "pf1: NLV" in out
    store = Stage3Store(tmp_path / "cio.db")
    state = store.get_account_state(report["request"]["account_state_id"])
    assert "-PF1-" in state.account_state_id
    assert state.constraints.max_trade_loss_eur == pytest.approx(state.account_equity_eur * 0.015, abs=0.01)
    version = SyncStore(tmp_path / "sync.db").get_version(report["account_state_version"])
    assert account_state_from_version(version).account_state_id == f"AS-{version.version_id}"  # no limits: old id


def test_ibkr_listing_implements_long_and_fails_closed_on_short():
    info = ContractInfo(conid=5001, symbol="UCG", exchange="SMART", primary_exchange="BVME", currency="EUR",
                        long_name="UNICREDIT SPA", sec_type="STK", stock_type="COMMON")
    instrument = ibkr_instrument("UCG.MI", info, as_of=NOW)
    assert instrument.instrument_id == "IBKR:5001" and instrument.instrument_type is InstrumentType.ORDINARY
    assert instrument.underlying == "UCG.MI" and instrument.quote_currency.value == "EUR"

    def opportunity(direction):
        return SimpleNamespace(opportunity_id="OPP-1", direction=direction, horizon=TradingHorizon.SWING,
                               ticker="UCG.MI")
    selector = InstrumentSelector()
    (long_candidate,) = selector.select(opportunity(Direction.LONG), [instrument])
    (short_candidate,) = selector.select(opportunity(Direction.SHORT), [instrument])
    assert long_candidate.eligible and not short_candidate.eligible

    with pytest.raises(Stage5DryRunError):
        ibkr_instrument("X", ContractInfo(conid=1, symbol="X", exchange="SMART", primary_exchange="SEHK",
                                          currency="HKD", long_name="X", sec_type="STK"), as_of=NOW)


def test_market_inputs_use_last_close_and_an_atr_stop():
    index = pd.bdate_range("2026-10-01", periods=20)
    history = pd.DataFrame({"High": [11.0] * 20, "Low": [9.0] * 20, "Close": [10.0] * 20}, index=index)
    assert average_true_range(history) == pytest.approx(2.0)
    long = market_inputs(history, direction=Direction.LONG, fx=1.0, now=NOW)
    short = market_inputs(history, direction=Direction.SHORT, fx=1.0, now=NOW, stop_atr_multiple=1.5)
    assert (long.reference_price, long.stop_price) == (10.0, 6.0)
    assert short.stop_price == 13.0
    assert long.market_observed_at <= NOW and long.market_observed_at.date() == index[-1].date()
    with pytest.raises(Stage5DryRunError):
        average_true_range(history.head(5))


def test_marginal_risk_inputs_come_from_the_ibkr_account_state(tmp_path, capsys):
    gateway, report, _ = run_part_a(tmp_path, capsys)
    store = Stage3Store(tmp_path / "cio.db")
    snapshot = store.get_portfolio_snapshot(report["configuration"]["portfolio_snapshot_id"])
    version = SyncStore(tmp_path / "sync.db").get_version(report["account_state_version"])
    assert fx_to_eur(version, "EUR") == 1.0 and fx_to_eur(version, "USD") == pytest.approx(0.86)
    session = RecordedGatewaySession(str(gateway))
    loader = IbkrHistoryLoader(session, directory_from_version(version), duration=RISK_HISTORY_DURATION)
    provider = IbkrMarginalRiskInputProvider(positions_from_version(version), loader,
                                             snapshot_reference=snapshot.source_file)
    candidate = SimpleNamespace(ticker="CCC", snapshot_id=snapshot.snapshot_id)
    inputs = provider(candidate, snapshot)
    assert {p.yahoo_symbol for p in inputs.analyzed_positions} == {"AAA", "BBB", "CCC"}
    assert inputs.candidate_symbol == "CCC" and inputs.benchmark_history.index.max() <= pd.Timestamp(
        snapshot.timestamp).tz_localize(None)
    result = PortfolioMarginalRiskEngine(
        benchmark_symbol=BENCHMARK_SYMBOL, min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    ).assess(analyzed_positions=inputs.analyzed_positions, candidate_symbol="CCC", direction="LONG",
             candidate_exposure_eur=500.0, candidate_history=inputs.candidate_history,
             benchmark_history=inputs.benchmark_history)
    assert result is not None

    other = snapshot.model_copy(update={"source_file": "ibkr://OTHER/v"})
    with pytest.raises(ValueError):
        provider(candidate, other)
    with pytest.raises(ValueError):
        provider(SimpleNamespace(ticker="CCC", snapshot_id="SNAP-OTHER"), snapshot)


def test_dry_run_stops_cleanly_when_part_a_has_no_selectable_opportunity(tmp_path, capsys):
    gateway, _, _ = run_part_a(tmp_path, capsys)
    code = s5_dry_run.main([
        "--output-directory", str(tmp_path / "out"), "--db", str(tmp_path / "cio.db"),
        "--sync-db", str(tmp_path / "sync.db"), "--gateway-recording", str(gateway),
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "selectable=[]" in out and "S5_DRY_RUN: NO_SELECTABLE_OPPORTUNITY" in out


def seed_opportunity(tmp_path, report, ticker="CCC"):
    """A provenance-complete S2.2F opportunity on part A's IBKR snapshot (what a
    scored research above threshold would materialize)."""
    from app.cio.models import OpportunityStatus, TradeOpportunity
    from app.scanner.research_integration_contracts import (
        HypothesisOutcomeReason, HypothesisOutcomeStatus, IntegrationRunStatus, ResearchHypothesisKind,
        ResearchHypothesisOutcome, ScannerResearchIntegrationPolicy, ScannerResearchRun,
        TradeOpportunityProvenanceLink,
    )
    from app.scanner.research_integration_store import ScannerResearchIntegrationStore

    db = tmp_path / "cio.db"
    stage3, integration = Stage3Store(db), ScannerResearchIntegrationStore(db)
    snapshot = stage3.get_portfolio_snapshot(report["configuration"]["portfolio_snapshot_id"])
    at = snapshot.timestamp
    opportunity = TradeOpportunity(opportunity_id="opp-ibkr", snapshot_id=snapshot.snapshot_id, created_at=at,
                                   ticker=ticker, direction=Direction.LONG, horizon=TradingHorizon.SWING,
                                   confidence=0.7, thesis="Controlled thesis", status=OpportunityStatus.DISCOVERED)
    stage3.save_trade_opportunity(opportunity)
    run = ScannerResearchRun(
        run_id="s2f-ibkr", watch_universe_run_id="watch", watch_universe_fingerprint="wfp",
        portfolio_snapshot_id=snapshot.snapshot_id, policy=ScannerResearchIntegrationPolicy(), as_of=at,
        started_at=at, completed_at=at, status=IntegrationRunStatus.COMPLETED, hypothesis_ids=("hyp",),
        completed_hypothesis_ids=("hyp",), opportunity_ids=("opp-ibkr",), fingerprint="fp")
    integration.save_run(run)
    integration.save_outcome(ResearchHypothesisOutcome(
        integration_run_id=run.run_id, hypothesis_id="hyp", subject_namespace="YAHOO", subject_value=ticker,
        kind=ResearchHypothesisKind.NEW_LONG, status=HypothesisOutcomeStatus.OPPORTUNITY_CREATED,
        reason=HypothesisOutcomeReason.OPPORTUNITY_CREATED, updated_at=at, research_id="r", opportunity_score_id="s",
        opportunity_id="opp-ibkr"))
    integration.save_provenance_link(TradeOpportunityProvenanceLink(
        opportunity_id="opp-ibkr", integration_run_id=run.run_id, watch_universe_run_id="watch",
        watch_universe_fingerprint="wfp", subject_namespace="YAHOO", subject_value=ticker, hypothesis_id="hyp",
        candidate_id="hyp", research_id="r", opportunity_score_id="s", portfolio_snapshot_id=snapshot.snapshot_id,
        evidence_ids=("e",), inference_ids=("i",), policy_id=run.policy.policy_id,
        policy_version=run.policy.policy_version, invalidation_conditions=("x",), created_at=at))
    report["run"]["research_run_id"] = run.run_id
    report["run"]["selectable_opportunity_ids"] = ["opp-ibkr"]
    path = tmp_path / "out" / "stage5_first_e2e_seeded.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def dry_run(tmp_path, capsys, *extra):
    gateway, report, _ = run_part_a(tmp_path, capsys)
    seeded = seed_opportunity(tmp_path, report)
    return s5_dry_run.main([
        "--report", str(seeded), "--output-directory", str(tmp_path / "out"), "--db", str(tmp_path / "cio.db"),
        "--sync-db", str(tmp_path / "sync.db"), "--gateway-recording", str(gateway), *extra,
    ])


def test_pf1_var_limit_blocks_an_oversized_trade(tmp_path, capsys):
    code = dry_run(tmp_path, capsys)  # default exposure: 10% of NLV on a highly correlated name
    out = capsys.readouterr().out
    assert code == 2 and "terminal_reason: PORTFOLIO_FILTER_BLOCKED" in out
    assert "portfolio_fit: REJECT" in out and "MAX_VAR_95_1D_EUR FAIL" in out
    assert "execution_plan_id: None" in out and "broker_orders_submitted: 0" in out


def test_dry_run_reaches_an_unauthorized_execution_plan_on_ibkr_data(tmp_path, capsys):
    code = dry_run(tmp_path, capsys, "--exposure-eur", "300")
    out = capsys.readouterr().out
    assert "instrument: IBKR:1003" in out and "stop " in out
    for counter in ("broker_orders_submitted", "portfolio_mutations", "automatic_executions"):
        assert f"{counter}: 0" in out
    (path,) = (tmp_path / "out" / "dry_run").glob("stage5_dry_run_*.json")
    dry = json.loads(path.read_text(encoding="utf-8"))
    assert dry["run"]["execution_authorized"] is False
    assert code == 0, out
    assert "S5_DRY_RUN: PASS" in out and dry["run"]["execution_plan_id"]
    assert "funded from EUR cash" in out


def test_trading_currency_funding_keeps_the_4_0_rule(tmp_path, capsys):
    code = dry_run(tmp_path, capsys, "--exposure-eur", "300", "--funding", "trading")
    out = capsys.readouterr().out  # the synthetic account holds no USD cash
    assert code == 1 and "POSITION_SIZING: FAILED" in out and "below one unit" in out


def test_completed_dry_run_writes_one_order_instruction_request(tmp_path, capsys):
    from app.e2e.stage5_order_instruction import load_request
    from tools import s5_record_instruction

    code = dry_run(tmp_path, capsys, "--exposure-eur", "300")
    out = capsys.readouterr().out
    assert code == 0, out
    (path,) = (tmp_path / "out" / "order_instructions").glob("oir-*.json")
    request = load_request(path)
    a = request.arguments
    assert a.contract_id_ex == "1003" and a.side == "BUY" and a.order_type == "LIMIT" and a.time_in_force == "DAY"
    assert a.quantity >= 1 and a.limit_price == pytest.approx(193.25)
    assert request.protective_stop is not None and request.protective_stop < a.limit_price
    assert request.simulated_state and request.requires_holder_confirmation and not request.rehearsal
    assert request.status == "PENDING_CREATION", request.blocked_reason
    assert "order_instructions_created: 0" in out and "broker_orders_submitted: 0" in out

    assert s5_record_instruction.main([str(path), "--instruction-id", "I-1", "--deep-link", "https://ibkr.example/i/1"]) == 0
    assert load_request(path).status == "CREATED" and load_request(path).instruction_id == "I-1"
    assert s5_record_instruction.main([str(path), "--instruction-id", "I-2", "--deep-link", "https://x"]) == 2


def test_order_instruction_guards():
    from app.cio.models import Currency, ExecutionPlan, ExecutionSide
    from app.e2e.stage5_order_instruction import build_request

    plan = ExecutionPlan(
        execution_plan_id="EXEC-1", created_at=NOW, opportunity_id="opp", proposal_id="p", simulation_id="s",
        decision_id="d", snapshot_id="snap", broker="IBKR", underlying="UCG.MI", instrument_id="IBKR:262695149",
        currency=Currency.EUR, direction=Direction.LONG, execution_side=ExecutionSide.BUY, quantity=6,
        order_type="LIMIT", reference_price=81.3, entry_price=81.3, stop_price=76.43, fx_to_eur=1.0,
        gross_exposure_eur=487.8)
    common = dict(dry_run_id="r", version_id="v", now=NOW)
    ok = build_request(plan, state_origin="REAL_IBKR", cash_available={"EUR": 2000.0}, rehearsal=False, **common)
    assert ok.status == "PENDING_CREATION" and not ok.requires_holder_confirmation
    assert ok.arguments.model_dump() == {"contract_id_ex": "262695149", "side": "BUY", "quantity": 6,
                                         "order_type": "LIMIT", "limit_price": 81.3, "time_in_force": "DAY"}
    poor = build_request(plan, state_origin="REAL_IBKR", cash_available={"EUR": 100.0}, rehearsal=False, **common)
    assert poor.status == "BLOCKED" and "exceeds the real IBKR cash" in poor.blocked_reason
    rehearsal = build_request(plan, state_origin="REAL_IBKR", cash_available={"EUR": 2000.0}, rehearsal=True, **common)
    assert rehearsal.status == "BLOCKED" and "REHEARSAL" in rehearsal.blocked_reason
    assert ok.request_id == build_request(plan, state_origin="REAL_IBKR", cash_available={"EUR": 2000.0},
                                          rehearsal=False, **common).request_id
