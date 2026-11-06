from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from app.analysis.marginal_risk import (
    MarginalMetricDelta,
    PortfolioMarginalRiskResult,
)
from app.cio.models import (
    Currency,
    Direction,
    ExecutionSide,
    PortfolioSnapshot,
    TradeProposal,
)
from app.cio.portfolio_simulation_marginal_risk import (
    PortfolioSimulationMarginalRiskAdapter,
)
from app.cio.portfolio_simulator import PortfolioRiskSimulator


NOW = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="SNAP-PF1G1",
        timestamp=NOW,
        source_file="data/input/portafoglio-export.xlsx",
        source_file_hash="PF1G1",
        quant_engine_version="2.5",
        analyzed_positions=1,
        gross_exposure_eur=100_000.0,
        net_exposure_eur=100_000.0,
        account_state_id="ACC-PF1G1",
    )


def _proposal() -> TradeProposal:
    return TradeProposal(
        proposal_id="PROP-PF1G1",
        opportunity_id="OPP-PF1G1",
        snapshot_id="SNAP-PF1G1",
        created_at=NOW,
        ticker="QCOM",
        direction=Direction.SHORT,
        instrument_id="FIN-QCOM",
        sizing_id="SIZ-PF1G1",
        execution_side=ExecutionSide.SELL_SHORT,
        quantity=10,
        reference_price=100.0,
        currency=Currency.USD,
        entry_type="MARKET",
        gross_exposure_eur=10_000.0,
    )


def _result() -> PortfolioMarginalRiskResult:
    return PortfolioMarginalRiskResult(
        candidate_symbol="QCOM",
        direction="SHORT",
        candidate_exposure_eur=10_000.0,
        volatility_pct=MarginalMetricDelta(15.0, 14.0, -1.0),
        beta=MarginalMetricDelta(0.8, 0.7, -0.1),
        var_95_1d_eur=MarginalMetricDelta(4000.0, 3800.0, -200.0),
        cvar_95_1d_eur=MarginalMetricDelta(6000.0, 5600.0, -400.0),
        candidate_correlation_to_portfolio=0.4,
        candidate_component_risk_pct_points=-0.5,
        candidate_risk_contribution_pct=-3.0,
        covariance_coverage_before_pct=90.0,
        covariance_coverage_after_pct=91.0,
        benchmark_coverage_before_pct=90.0,
        benchmark_coverage_after_pct=91.0,
        tail_coverage_before_pct=90.0,
        tail_coverage_after_pct=91.0,
        analytical_coverage_before_pct=90.0,
        analytical_coverage_after_pct=91.0,
        excluded_symbols_before=(),
        excluded_symbols_after=(),
        observations_before=600,
        observations_after=600,
    )


class RecordingProvider:
    def __init__(self):
        self.calls = []

    def __call__(self, candidate, snapshot):
        self.calls.append((candidate, snapshot))
        return SimpleNamespace(
            analyzed_positions=["PORTFOLIO"],
            candidate_symbol="QCOM",
            candidate_history="CANDIDATE_HISTORY",
            benchmark_history="BENCHMARK_HISTORY",
        )


class RecordingEngine:
    def __init__(self):
        self.kwargs = None

    def assess(self, **kwargs):
        self.kwargs = kwargs
        return _result()


def test_pf1g1_adapter_maps_exact_trade_proposal_to_shared_engine():
    provider = RecordingProvider()
    engine = RecordingEngine()
    adapter = PortfolioSimulationMarginalRiskAdapter(
        engine=engine,
        input_provider=provider,
    )

    snapshot = _snapshot()
    proposal = _proposal()

    result = adapter.assess(
        snapshot=snapshot,
        proposal=proposal,
    )

    assert result is not None
    assert provider.calls == [(proposal, snapshot)]
    assert engine.kwargs == {
        "analyzed_positions": ["PORTFOLIO"],
        "candidate_symbol": "QCOM",
        "direction": "SHORT",
        "candidate_exposure_eur": 10_000.0,
        "candidate_history": "CANDIDATE_HISTORY",
        "benchmark_history": "BENCHMARK_HISTORY",
    }


def test_pf1g1_adapter_rejects_snapshot_mismatch_before_provider_call():
    provider = RecordingProvider()
    engine = RecordingEngine()
    adapter = PortfolioSimulationMarginalRiskAdapter(
        engine=engine,
        input_provider=provider,
    )

    proposal = _proposal()
    bad_snapshot = _snapshot().model_copy(
        update={"snapshot_id": "SNAP-OTHER"}
    )

    try:
        adapter.assess(snapshot=bad_snapshot, proposal=proposal)
    except ValueError as exc:
        assert "snapshot_id" in str(exc)
    else:
        raise AssertionError("Expected snapshot mismatch to fail")

    assert provider.calls == []
    assert engine.kwargs is None


def test_pf1g1_simulator_accepts_injected_marginal_risk_boundary():
    marker = object()
    simulator = PortfolioRiskSimulator(
        marginal_risk_adapter=marker,
    )
    assert simulator.marginal_risk_adapter is marker


def test_pf1g1_default_simulator_remains_backward_compatible():
    simulator = PortfolioRiskSimulator()
    assert simulator.marginal_risk_adapter is None
