from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.analysis.marginal_risk import (
    PortfolioMarginalRiskEngine,
    PortfolioMarginalRiskResult,
)
from app.cio.models import PortfolioSnapshot, TradeProposal


class PortfolioSimulationMarginalRiskInputProvider(Protocol):
    """
    Structural input-provider contract for PortfolioRiskSimulator V2.

    The provider must return an object exposing the four canonical fields
    consumed by PortfolioMarginalRiskEngine:
      - analyzed_positions
      - candidate_symbol
      - candidate_history
      - benchmark_history

    CanonicalPortfolioMarginalRiskInputProvider already satisfies this
    contract at runtime because TradeProposal and TradeOpportunity both expose
    snapshot_id and ticker, which are the only candidate fields used by the
    canonical default provider path.
    """

    def __call__(
        self,
        candidate: TradeProposal,
        snapshot: PortfolioSnapshot,
    ) -> object:
        ...


@dataclass(frozen=True)
class PortfolioSimulationMarginalRiskAdapter:
    """
    PF-1G boundary between an exact TradeProposal and the shared PF-1C
    PortfolioMarginalRiskEngine.

    This adapter deliberately contains no risk formula. It only:
      1. obtains canonical snapshot/candidate market inputs;
      2. maps exact TradeProposal direction and gross exposure;
      3. delegates to PortfolioMarginalRiskEngine.

    PF-1G.1 introduces the boundary only. PortfolioRiskSimulator V1 output
    semantics remain unchanged until PF-1G.2 consumes this result.
    """

    engine: PortfolioMarginalRiskEngine
    input_provider: PortfolioSimulationMarginalRiskInputProvider

    def assess(
        self,
        *,
        snapshot: PortfolioSnapshot,
        proposal: TradeProposal,
    ) -> PortfolioMarginalRiskResult:
        if proposal.snapshot_id != snapshot.snapshot_id:
            raise ValueError(
                "TradeProposal snapshot_id does not match PortfolioSnapshot"
            )

        inputs = self.input_provider(proposal, snapshot)

        analyzed_positions = getattr(inputs, "analyzed_positions")
        candidate_symbol = getattr(inputs, "candidate_symbol")
        candidate_history = getattr(inputs, "candidate_history")
        benchmark_history = getattr(inputs, "benchmark_history")

        return self.engine.assess(
            analyzed_positions=analyzed_positions,
            candidate_symbol=candidate_symbol,
            direction=proposal.direction.value,
            candidate_exposure_eur=proposal.gross_exposure_eur,
            candidate_history=candidate_history,
            benchmark_history=benchmark_history,
        )
