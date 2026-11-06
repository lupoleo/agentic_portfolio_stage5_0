from __future__ import annotations

from types import SimpleNamespace

from app.cio.portfolio_simulation_factory import (
    build_canonical_portfolio_simulation_service,
)
from app.cio.portfolio_simulation_service import (
    PortfolioSimulationService,
)
from app.cio.portfolio_simulator import PortfolioRiskSimulator


class DummyStore:
    pass


class RecordingSimulator:
    def __init__(self):
        self.calls = []

    def simulate(self, **kwargs):
        self.calls.append(kwargs)
        return "SIMULATION"


def test_pf1g5_service_allows_explicit_simulator_injection():
    store = DummyStore()
    simulator = RecordingSimulator()

    service = PortfolioSimulationService(
        store,
        simulator=simulator,
    )

    assert service.store is store
    assert service.simulator is simulator


def test_pf1g5_direct_service_construction_preserves_legacy_v1_boundary():
    service = PortfolioSimulationService(DummyStore())

    assert isinstance(service.simulator, PortfolioRiskSimulator)
    assert service.simulator.marginal_risk_adapter is None


def test_pf1g5_canonical_factory_builds_v2_simulator():
    service = build_canonical_portfolio_simulation_service(
        DummyStore()
    )

    assert isinstance(service, PortfolioSimulationService)
    assert isinstance(service.simulator, PortfolioRiskSimulator)

    adapter = service.simulator.marginal_risk_adapter

    assert adapter is not None
    assert adapter.engine is not None
    assert adapter.input_provider is not None


def test_pf1g5_canonical_factory_uses_shared_engine_constants():
    service = build_canonical_portfolio_simulation_service(
        DummyStore()
    )

    engine = service.simulator.marginal_risk_adapter.engine

    assert engine.min_observations == 504
    assert engine.max_observations == 756


def test_pf1g5_canonical_provider_accepts_tradeproposal_structural_contract():
    service = build_canonical_portfolio_simulation_service(
        DummyStore()
    )

    provider = service.simulator.marginal_risk_adapter.input_provider

    proposal = SimpleNamespace(
        snapshot_id="SNAP-X",
        ticker="qcom",
    )

    assert provider.candidate_symbol_resolver(proposal) == "QCOM"
