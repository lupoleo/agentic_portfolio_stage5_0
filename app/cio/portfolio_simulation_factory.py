from __future__ import annotations

from typing import Callable

from app.analysis.marginal_risk import PortfolioMarginalRiskEngine
from app.analysis.portfolio import (
    BENCHMARK_SYMBOL,
    RISK_MAX_SESSIONS,
    RISK_MIN_COMMON_OBSERVATIONS,
)
from app.cio.models import TradeProposal
from app.cio.portfolio_marginal_risk_provider import (
    CanonicalPortfolioMarginalRiskInputProvider,
)
from app.cio.portfolio_simulation_marginal_risk import (
    PortfolioSimulationMarginalRiskAdapter,
)
from app.cio.portfolio_simulation_service import (
    PortfolioSimulationService,
)
from app.cio.portfolio_simulator import PortfolioRiskSimulator
from app.cio.storage import Stage3Store


def build_canonical_portfolio_simulation_service(
    store: Stage3Store,
    *,
    candidate_symbol_resolver: Callable[[TradeProposal], str] | None = None,
) -> PortfolioSimulationService:
    """Production composition root for Portfolio Simulator V2."""

    provider = CanonicalPortfolioMarginalRiskInputProvider(
        candidate_symbol_resolver=candidate_symbol_resolver,
    )

    engine = PortfolioMarginalRiskEngine(
        benchmark_symbol=BENCHMARK_SYMBOL,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )

    adapter = PortfolioSimulationMarginalRiskAdapter(
        engine=engine,
        input_provider=provider,
    )

    simulator = PortfolioRiskSimulator(
        marginal_risk_adapter=adapter,
    )

    return PortfolioSimulationService(
        store,
        simulator=simulator,
    )
