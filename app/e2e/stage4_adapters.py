"""Thin adapter boundary around already-frozen Stage 4 business services."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.e2e.stage4_contracts import (
    Stage4E2ERequest,
    Stage4E2ERun,
    Stage4Stage,
    Stage4StageResult,
)


StageAdapter = Callable[
    [Stage4E2ERequest, Stage4E2ERun],
    Stage4StageResult,
]


@dataclass(frozen=True)
class ExistingStage4ServicesAdapter:
    """Delegates to existing services without changing their contracts."""

    portfolio_analysis: StageAdapter
    scanner_discovery: StageAdapter
    watch_universe_assembly: StageAdapter
    research_integration: StageAdapter
    selected_opportunity_dry_run: StageAdapter

    def execute(
        self,
        stage: Stage4Stage,
        request: Stage4E2ERequest,
        run: Stage4E2ERun,
    ) -> Stage4StageResult:
        delegates = {
            Stage4Stage.PORTFOLIO_ANALYSIS: self.portfolio_analysis,
            Stage4Stage.SCANNER_DISCOVERY: self.scanner_discovery,
            Stage4Stage.WATCH_UNIVERSE_ASSEMBLY: self.watch_universe_assembly,
            Stage4Stage.RESEARCH_INTEGRATION: self.research_integration,
            Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN: (
                self.selected_opportunity_dry_run
            ),
        }
        if stage not in delegates:
            raise ValueError(f"{stage.value} is owned by the E2E orchestrator")
        result = delegates[stage](request, run)
        if not isinstance(result, Stage4StageResult):
            raise TypeError(f"adapter for {stage.value} returned an invalid result")
        if result.stage is not stage:
            raise ValueError(
                f"adapter returned {result.stage.value} for {stage.value}"
            )
        return result
