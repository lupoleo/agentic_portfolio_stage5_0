from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, tanh

from app.cio.models import (
    Direction,
    PortfolioExposureContext,
    PortfolioMarginalRiskContext,
    PortfolioRiskState,
)


@dataclass(frozen=True)
class PortfolioFitComponentScore:
    name: str
    weight: float
    score: float | None
    reason: str

    @property
    def available(self) -> bool:
        return self.score is not None


@dataclass(frozen=True)
class PortfolioFitScoringResult:
    portfolio_fit_score: float | None
    scoring_coverage_pct: float
    analytical_coverage_pct: float | None
    components: tuple[PortfolioFitComponentScore, ...]

    def component(self, name: str) -> PortfolioFitComponentScore:
        for item in self.components:
            if item.name == name:
                return item
        raise KeyError(name)


class PortfolioFitScoringEngine:
    """
    PF-1D.2 pure deterministic scoring kernel.

    Mathematical contract is frozen by PF-1D.1:
      directional     10%
      concentration   15%
      correlation     15%
      volatility      20%
      beta            15%
      tail risk       25% (40% VaR / 60% CVaR)

    Neutral score is 50. UNKNOWN components are excluded from the weighted
    denominator; they are never silently converted to 50.
    """

    WEIGHTS = {
        "directional": 0.10,
        "concentration": 0.15,
        "correlation": 0.15,
        "volatility": 0.20,
        "beta": 0.15,
        "tail_risk": 0.25,
    }
    MIN_SCORING_COVERAGE_PCT = 70.0
    BETA_EPSILON = 0.05

    def score(
        self,
        *,
        direction: Direction,
        target_exposure_eur: float | None,
        before: PortfolioRiskState,
        exposure_context: PortfolioExposureContext | None,
        marginal_risk_context: PortfolioMarginalRiskContext | None,
    ) -> PortfolioFitScoringResult:
        components = (
            self._directional(target_exposure_eur, exposure_context),
            self._concentration(target_exposure_eur, before, exposure_context),
            self._correlation(direction, before, marginal_risk_context),
            self._volatility(target_exposure_eur, before, marginal_risk_context),
            self._beta(target_exposure_eur, before, marginal_risk_context),
            self._tail_risk(target_exposure_eur, before, marginal_risk_context),
        )

        available_weight = sum(x.weight for x in components if x.available)
        coverage = 100.0 * available_weight

        if coverage + 1e-12 < self.MIN_SCORING_COVERAGE_PCT:
            fit = None
        else:
            weighted = sum(x.weight * x.score for x in components if x.score is not None)
            fit = self._clamp(weighted / available_weight)

        analytical = (
            marginal_risk_context.analytical_coverage_after_pct
            if marginal_risk_context is not None
            else before.analytical_coverage_pct
        )

        return PortfolioFitScoringResult(
            portfolio_fit_score=fit,
            scoring_coverage_pct=coverage,
            analytical_coverage_pct=analytical,
            components=components,
        )

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(100.0, float(value)))

    @staticmethod
    def _finite_positive(value: float | None) -> bool:
        return value is not None and isfinite(value) and value > 0.0

    def _exposure_ratio(self, target: float | None, gross_before: float) -> float | None:
        if not self._finite_positive(target) or not self._finite_positive(gross_before):
            return None
        return float(target) / float(gross_before)

    def _risk_score(self, marginal_efficiency: float) -> float:
        return self._clamp(50.0 - 50.0 * tanh(marginal_efficiency))

    def _relative_efficiency(
        self,
        *,
        before_value: float,
        after_value: float,
        exposure_ratio: float | None,
        denominator_floor: float | None = None,
        absolute_metric: bool = False,
    ) -> float | None:
        if exposure_ratio is None or exposure_ratio <= 0:
            return None

        b = abs(before_value) if absolute_metric else before_value
        a = abs(after_value) if absolute_metric else after_value
        denominator = abs(b)
        if denominator_floor is not None:
            denominator = max(denominator, denominator_floor)
        if denominator <= 0 or not all(isfinite(v) for v in (a, b, denominator)):
            return None
        relative_delta = (a - b) / denominator
        return relative_delta / exposure_ratio

    def _directional(self, target, ctx):
        w = self.WEIGHTS["directional"]
        if (
            not self._finite_positive(target)
            or ctx is None
            or ctx.absolute_net_exposure_after_eur is None
        ):
            return PortfolioFitComponentScore("directional", w, None, "UNKNOWN: projected net exposure is unavailable.")
        d = (
            ctx.absolute_net_exposure_after_eur
            - ctx.absolute_net_exposure_before_eur
        ) / float(target)
        score = self._clamp(50.0 - 50.0 * d)
        return PortfolioFitComponentScore("directional", w, score, f"normalized_abs_net_delta={d:.12g}")

    def _concentration(self, target, before, ctx):
        w = self.WEIGHTS["concentration"]
        if (
            not self._finite_positive(target)
            or ctx is None
            or not self._finite_positive(ctx.gross_exposure_after_eur)
            or not self._finite_positive(before.effective_positions)
        ):
            return PortfolioFitComponentScore("concentration", w, None, "UNKNOWN: gross-after or effective positions unavailable.")
        candidate_weight = float(target) / ctx.gross_exposure_after_eur
        effective_average_weight = 1.0 / before.effective_positions
        ratio = candidate_weight / effective_average_weight
        score = self._clamp(100.0 / (1.0 + ratio))
        return PortfolioFitComponentScore("concentration", w, score, f"candidate_to_effective_average_ratio={ratio:.12g}")

    def _correlation(self, direction, before, marginal):
        w = self.WEIGHTS["correlation"]
        if marginal is None or marginal.candidate_correlation_to_portfolio is None:
            return PortfolioFitComponentScore("correlation", w, None, "UNKNOWN: candidate portfolio correlation unavailable.")
        net_sign = 1.0 if before.net_exposure_eur > 0 else (-1.0 if before.net_exposure_eur < 0 else 0.0)
        direction_sign = 1.0 if direction == Direction.LONG else -1.0
        effective = marginal.candidate_correlation_to_portfolio * net_sign * direction_sign
        score = self._clamp(50.0 * (1.0 - effective))
        return PortfolioFitComponentScore("correlation", w, score, f"effective_correlation={effective:.12g}")

    def _volatility(self, target, before, marginal):
        w = self.WEIGHTS["volatility"]
        if marginal is None:
            return PortfolioFitComponentScore("volatility", w, None, "UNKNOWN: marginal volatility unavailable.")
        e = self._exposure_ratio(target, before.gross_exposure_eur)
        m = self._relative_efficiency(
            before_value=marginal.volatility_pct.before,
            after_value=marginal.volatility_pct.after,
            exposure_ratio=e,
        )
        if m is None:
            return PortfolioFitComponentScore("volatility", w, None, "UNKNOWN: volatility efficiency cannot be normalized.")
        return PortfolioFitComponentScore("volatility", w, self._risk_score(m), f"marginal_risk_efficiency={m:.12g}")

    def _beta(self, target, before, marginal):
        w = self.WEIGHTS["beta"]
        if marginal is None:
            return PortfolioFitComponentScore("beta", w, None, "UNKNOWN: marginal beta unavailable.")
        e = self._exposure_ratio(target, before.gross_exposure_eur)
        m = self._relative_efficiency(
            before_value=marginal.beta.before,
            after_value=marginal.beta.after,
            exposure_ratio=e,
            denominator_floor=self.BETA_EPSILON,
            absolute_metric=True,
        )
        if m is None:
            return PortfolioFitComponentScore("beta", w, None, "UNKNOWN: beta efficiency cannot be normalized.")
        return PortfolioFitComponentScore("beta", w, self._risk_score(m), f"marginal_abs_beta_efficiency={m:.12g}")

    def _tail_risk(self, target, before, marginal):
        w = self.WEIGHTS["tail_risk"]
        if marginal is None:
            return PortfolioFitComponentScore("tail_risk", w, None, "UNKNOWN: marginal tail risk unavailable.")
        e = self._exposure_ratio(target, before.gross_exposure_eur)
        m_var = self._relative_efficiency(
            before_value=marginal.var_95_1d_eur.before,
            after_value=marginal.var_95_1d_eur.after,
            exposure_ratio=e,
        )
        m_cvar = self._relative_efficiency(
            before_value=marginal.cvar_95_1d_eur.before,
            after_value=marginal.cvar_95_1d_eur.after,
            exposure_ratio=e,
        )
        if m_var is None or m_cvar is None:
            return PortfolioFitComponentScore("tail_risk", w, None, "UNKNOWN: VaR/CVaR efficiency cannot be normalized.")
        s_var = self._risk_score(m_var)
        s_cvar = self._risk_score(m_cvar)
        score = 0.40 * s_var + 0.60 * s_cvar
        return PortfolioFitComponentScore(
            "tail_risk", w, self._clamp(score),
            f"var_efficiency={m_var:.12g}; cvar_efficiency={m_cvar:.12g}; var_weight=0.40; cvar_weight=0.60",
        )
