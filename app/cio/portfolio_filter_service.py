from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Callable

from app.analysis.marginal_risk import (
    PortfolioMarginalRiskEngine,
    PortfolioMarginalRiskResult,
)
from uuid import uuid4

from app.cio.models import (
    AccountState, Direction, PortfolioCheckStatus, PortfolioConstraintCheck,
    PortfolioDirectionalEffect, PortfolioExposureContext,
    PortfolioFitAssessment, PortfolioFitDecision,
    PortfolioMarginalRiskContext, PortfolioMetricDelta,
    PortfolioFitScoreBreakdown, PortfolioFitScoreComponent,
    PortfolioRiskState, PortfolioSnapshot, TradeOpportunity,
)
from app.cio.storage import Stage3Store
from app.cio.portfolio_fit_scoring import PortfolioFitScoringEngine


@dataclass(frozen=True)
class PortfolioMarginalRiskInputs:
    analyzed_positions: list
    candidate_symbol: str
    candidate_history: object
    benchmark_history: object


class PortfolioFilterService:
    CHECK_TARGET_EXPOSURE = "TARGET_EXPOSURE_AVAILABLE"
    CHECK_MAX_TRADE_LOSS = "MAX_TRADE_LOSS_EUR"
    CHECK_MAX_POSITION_WEIGHT = "MAX_POSITION_WEIGHT_PCT"
    CHECK_MAX_GROSS_EXPOSURE = "MAX_PORTFOLIO_GROSS_EXPOSURE_PCT"
    CHECK_MAX_CIO_DEPLOYABLE = "MAX_CIO_DEPLOYABLE_PCT"
    CHECK_MAX_PORTFOLIO_BETA = "MAX_PORTFOLIO_BETA"
    CHECK_MAX_VAR_95_1D = "MAX_VAR_95_1D_EUR"
    CHECK_MIN_CASH_RESERVE_EUR = "MIN_CASH_RESERVE_EUR"
    CHECK_MIN_CASH_RESERVE_USD = "MIN_CASH_RESERVE_USD"

    def __init__(
        self,
        store: Stage3Store,
        *,
        marginal_risk_engine: PortfolioMarginalRiskEngine | None = None,
        marginal_risk_input_provider: Callable | None = None,
        scoring_engine: PortfolioFitScoringEngine | None = None,
    ) -> None:
        self.store = store
        self.marginal_risk_engine = marginal_risk_engine
        self.marginal_risk_input_provider = marginal_risk_input_provider
        self.scoring_engine = scoring_engine


    def assess_and_persist(
        self,
        opportunity_id: str,
        *,
        as_of: datetime | None = None,
        assessment_id: str | None = None,
    ) -> PortfolioFitAssessment:
        """
        Run the deterministic portfolio-fit assessment and persist the
        exact returned artifact in Stage3Store.

        Plain assess() remains side-effect free. Persistence failures are
        intentionally propagated to the caller.
        """
        assessment = self.assess(
            opportunity_id,
            as_of=as_of,
            assessment_id=assessment_id,
        )
        self.store.save_portfolio_fit_assessment(assessment)
        return assessment

    def assess(self, opportunity_id: str, *, as_of: datetime | None = None, assessment_id: str | None = None) -> PortfolioFitAssessment:
        opportunity = self._require_opportunity(opportunity_id)
        snapshot = self._require_snapshot(opportunity)
        risk_record = self._require_risk_state(snapshot)
        before = risk_record.state
        self._validate_snapshot_risk_consistency(snapshot, before)
        account_state = self._resolve_account_state(snapshot)
        marginal_risk_context, marginal_warning = self._build_marginal_risk_context(
            opportunity, snapshot
        )
        checks = self._build_checks(
            opportunity, before, account_state, marginal_risk_context
        )
        exposure_context = self._build_exposure_context(
            opportunity, before, account_state
        )
        scoring_result = None
        if self.scoring_engine is not None:
            scoring_result = self.scoring_engine.score(
                direction=opportunity.direction,
                target_exposure_eur=opportunity.target_exposure_eur,
                before=before,
                exposure_context=exposure_context,
                marginal_risk_context=marginal_risk_context,
            )
        warnings = []
        if marginal_warning:
            warnings.append(marginal_warning)
        if before.analytical_coverage_pct is not None and before.analytical_coverage_pct < 100.0:
            warnings.append(f"Portfolio analytical coverage is {before.analytical_coverage_pct:.2f}%.")
        warnings.extend(check.reason for check in checks if check.status == PortfolioCheckStatus.UNKNOWN and check.reason)
        warnings = list(dict.fromkeys(warnings))
        decision = self._derive_decision(checks, warnings, scoring_result)
        now = as_of or datetime.now(timezone.utc)
        return PortfolioFitAssessment(
            assessment_id=assessment_id or self._new_assessment_id(opportunity.ticker, now),
            opportunity_id=opportunity.opportunity_id, snapshot_id=snapshot.snapshot_id,
            risk_state_id=risk_record.risk_state_id, account_state_id=account_state.account_state_id,
            created_at=now, ticker=opportunity.ticker.upper(), direction=opportunity.direction,
            target_exposure_eur=opportunity.target_exposure_eur, decision=decision,
            constraint_checks=checks, warnings=warnings,
            portfolio_fit_score=(
                scoring_result.portfolio_fit_score
                if scoring_result is not None else None
            ),
            rationale=self._build_rationale(decision, checks, scoring_result),
            exposure_context=exposure_context,
            marginal_risk_context=marginal_risk_context,
            score_breakdown=(
                PortfolioFitScoreBreakdown(
                    scoring_coverage_pct=scoring_result.scoring_coverage_pct,
                    analytical_coverage_pct=scoring_result.analytical_coverage_pct,
                    components=[
                        PortfolioFitScoreComponent(
                            name=item.name,
                            weight=item.weight,
                            score=item.score,
                            reason=item.reason,
                        )
                        for item in scoring_result.components
                    ],
                )
                if scoring_result is not None else None
            ),
        )

    def _require_opportunity(self, opportunity_id: str) -> TradeOpportunity:
        x = self.store.get_trade_opportunity(opportunity_id)
        if x is None: raise ValueError(f"TradeOpportunity not found: {opportunity_id}")
        return x

    def _require_snapshot(self, opportunity: TradeOpportunity) -> PortfolioSnapshot:
        x = self.store.get_portfolio_snapshot(opportunity.snapshot_id)
        if x is None: raise ValueError(f"PortfolioSnapshot not found: {opportunity.snapshot_id}")
        return x

    def _require_risk_state(self, snapshot: PortfolioSnapshot):
        x = self.store.get_latest_portfolio_risk_state(snapshot.snapshot_id)
        if x is None: raise ValueError(f"No PortfolioRiskStateRecord exists for PortfolioSnapshot {snapshot.snapshot_id}. Run the Portfolio Analysis / Quant Engine first.")
        return x

    def _resolve_account_state(self, snapshot: PortfolioSnapshot) -> AccountState:
        x = self.store.get_latest_account_state()
        if x is None and snapshot.account_state_id is not None:
            x = self.store.get_account_state(snapshot.account_state_id)
        if x is None: raise ValueError("No AccountState is available for portfolio filtering")
        return x

    def _validate_snapshot_risk_consistency(self, snapshot: PortfolioSnapshot, before: PortfolioRiskState) -> None:
        tol = 0.01
        if abs(before.gross_exposure_eur - snapshot.gross_exposure_eur) > tol:
            raise ValueError("PortfolioRiskState gross exposure does not match PortfolioSnapshot gross exposure")
        if abs(before.net_exposure_eur - snapshot.net_exposure_eur) > tol:
            raise ValueError("PortfolioRiskState net exposure does not match PortfolioSnapshot net exposure")
        if abs((before.long_exposure_eur + before.short_exposure_eur) - before.gross_exposure_eur) > tol:
            raise ValueError("PortfolioRiskState gross exposure is inconsistent with long + short exposure")
        if abs((before.long_exposure_eur - before.short_exposure_eur) - before.net_exposure_eur) > tol:
            raise ValueError("PortfolioRiskState net exposure is inconsistent with long - short exposure")

    def _build_marginal_risk_context(
        self,
        opportunity: TradeOpportunity,
        snapshot: PortfolioSnapshot,
    ):
        if (
            self.marginal_risk_engine is None
            or self.marginal_risk_input_provider is None
            or opportunity.target_exposure_eur is None
        ):
            return None, None

        try:
            inputs = self.marginal_risk_input_provider(opportunity, snapshot)
            if not isinstance(inputs, PortfolioMarginalRiskInputs):
                raise TypeError(
                    "marginal_risk_input_provider must return PortfolioMarginalRiskInputs"
                )

            result = self.marginal_risk_engine.assess(
                analyzed_positions=inputs.analyzed_positions,
                candidate_symbol=inputs.candidate_symbol,
                direction=opportunity.direction.value,
                candidate_exposure_eur=opportunity.target_exposure_eur,
                candidate_history=inputs.candidate_history,
                benchmark_history=inputs.benchmark_history,
            )
            return self._map_marginal_risk_result(result), None

        except Exception as exc:
            return (
                None,
                "Marginal risk could not be evaluated: "
                f"{type(exc).__name__}: {exc}",
            )

    def _map_marginal_risk_result(
        self,
        result: PortfolioMarginalRiskResult,
    ) -> PortfolioMarginalRiskContext:
        return PortfolioMarginalRiskContext(
            candidate_symbol=result.candidate_symbol,
            volatility_pct=PortfolioMetricDelta(
                before=result.volatility_pct.before,
                after=result.volatility_pct.after,
                delta=result.volatility_pct.delta,
            ),
            beta=PortfolioMetricDelta(
                before=result.beta.before,
                after=result.beta.after,
                delta=result.beta.delta,
            ),
            var_95_1d_eur=PortfolioMetricDelta(
                before=result.var_95_1d_eur.before,
                after=result.var_95_1d_eur.after,
                delta=result.var_95_1d_eur.delta,
            ),
            cvar_95_1d_eur=PortfolioMetricDelta(
                before=result.cvar_95_1d_eur.before,
                after=result.cvar_95_1d_eur.after,
                delta=result.cvar_95_1d_eur.delta,
            ),
            candidate_correlation_to_portfolio=result.candidate_correlation_to_portfolio,
            candidate_component_risk_pct_points=result.candidate_component_risk_pct_points,
            candidate_risk_contribution_pct=result.candidate_risk_contribution_pct,
            analytical_coverage_before_pct=result.analytical_coverage_before_pct,
            analytical_coverage_after_pct=result.analytical_coverage_after_pct,
            excluded_symbols_before=result.excluded_symbols_before,
            excluded_symbols_after=result.excluded_symbols_after,
            observations_before=result.observations_before,
            observations_after=result.observations_after,
        )

    def _build_exposure_context(
        self,
        opportunity: TradeOpportunity,
        before: PortfolioRiskState,
        account_state: AccountState,
    ) -> PortfolioExposureContext:
        target = opportunity.target_exposure_eur
        nav = account_state.canonical_nav_eur
        gross_before_pct_nav = (
            before.gross_exposure_eur / nav * 100.0
            if nav is not None else None
        )

        if target is None:
            return PortfolioExposureContext(
                long_exposure_before_eur=before.long_exposure_eur,
                short_exposure_before_eur=before.short_exposure_eur,
                gross_exposure_before_eur=before.gross_exposure_eur,
                net_exposure_before_eur=before.net_exposure_eur,
                gross_exposure_before_pct_nav=gross_before_pct_nav,
                absolute_net_exposure_before_eur=abs(before.net_exposure_eur),
                directional_effect=PortfolioDirectionalEffect.UNKNOWN,
            )

        if opportunity.direction == Direction.LONG:
            long_after = before.long_exposure_eur + target
            short_after = before.short_exposure_eur
        else:
            long_after = before.long_exposure_eur
            short_after = before.short_exposure_eur + target

        gross_after = long_after + short_after
        net_after = long_after - short_after
        abs_before = abs(before.net_exposure_eur)
        abs_after = abs(net_after)
        tol = 0.01

        if abs_after < abs_before - tol:
            effect = PortfolioDirectionalEffect.DIVERSIFICATION
        elif abs_after > abs_before + tol:
            effect = PortfolioDirectionalEffect.CONCENTRATION
        else:
            effect = PortfolioDirectionalEffect.NEUTRAL

        return PortfolioExposureContext(
            long_exposure_before_eur=before.long_exposure_eur,
            short_exposure_before_eur=before.short_exposure_eur,
            gross_exposure_before_eur=before.gross_exposure_eur,
            net_exposure_before_eur=before.net_exposure_eur,
            long_exposure_after_eur=long_after,
            short_exposure_after_eur=short_after,
            gross_exposure_after_eur=gross_after,
            net_exposure_after_eur=net_after,
            gross_exposure_before_pct_nav=gross_before_pct_nav,
            gross_exposure_after_pct_nav=(
                gross_after / nav * 100.0 if nav is not None else None
            ),
            absolute_net_exposure_before_eur=abs_before,
            absolute_net_exposure_after_eur=abs_after,
            directional_effect=effect,
        )

    def _na(self, code, unit):
        return PortfolioConstraintCheck(code=code, status=PortfolioCheckStatus.NOT_APPLICABLE, unit=unit, reason="Constraint is not configured in AccountState.")

    def _unknown_or_na(self, code, configured_value, unit, reason):
        if configured_value is None: return self._na(code, unit)
        return PortfolioConstraintCheck(code=code, status=PortfolioCheckStatus.UNKNOWN, limit_value=configured_value, unit=unit, reason=reason)

    def _build_checks(
        self,
        opportunity,
        before,
        account_state,
        marginal_risk_context=None,
    ):
        target = opportunity.target_exposure_eur
        c = account_state.constraints
        nav = account_state.canonical_nav_eur
        out = [PortfolioConstraintCheck(code=self.CHECK_TARGET_EXPOSURE, status=PortfolioCheckStatus.PASS if target is not None else PortfolioCheckStatus.UNKNOWN, projected_value=target, unit="EUR", reason=None if target is not None else "Portfolio Filter cannot evaluate exposure-based constraints because TradeOpportunity target_exposure_eur is UNKNOWN.")]

        if c.max_trade_loss_eur is None: out.append(self._na(self.CHECK_MAX_TRADE_LOSS, "EUR"))
        elif opportunity.max_intended_loss_eur is None: out.append(PortfolioConstraintCheck(code=self.CHECK_MAX_TRADE_LOSS, status=PortfolioCheckStatus.UNKNOWN, limit_value=c.max_trade_loss_eur, unit="EUR", reason="Max trade loss cannot be evaluated because TradeOpportunity max_intended_loss_eur is UNKNOWN."))
        else:
            v=opportunity.max_intended_loss_eur; ok=v <= c.max_trade_loss_eur
            out.append(PortfolioConstraintCheck(code=self.CHECK_MAX_TRADE_LOSS, status=PortfolioCheckStatus.PASS if ok else PortfolioCheckStatus.FAIL, projected_value=v, limit_value=c.max_trade_loss_eur, unit="EUR", reason=None if ok else "Max trade loss constraint exceeded."))

        if c.max_position_weight_pct is None: out.append(self._na(self.CHECK_MAX_POSITION_WEIGHT, "PCT_GROSS_AFTER"))
        elif target is None: out.append(PortfolioConstraintCheck(code=self.CHECK_MAX_POSITION_WEIGHT, status=PortfolioCheckStatus.UNKNOWN, limit_value=c.max_position_weight_pct, unit="PCT_GROSS_AFTER", reason="Max position weight cannot be evaluated because target_exposure_eur is UNKNOWN."))
        else:
            gross_after=before.gross_exposure_eur+target; pct=(target/gross_after*100.0) if gross_after>0 else 0.0; ok=pct<=c.max_position_weight_pct
            out.append(PortfolioConstraintCheck(code=self.CHECK_MAX_POSITION_WEIGHT,status=PortfolioCheckStatus.PASS if ok else PortfolioCheckStatus.FAIL,projected_value=pct,limit_value=c.max_position_weight_pct,unit="PCT_GROSS_AFTER",reason=None if ok else "Max position weight constraint exceeded."))

        if c.max_portfolio_gross_exposure_pct is None: out.append(self._na(self.CHECK_MAX_GROSS_EXPOSURE, "PCT_NAV"))
        elif target is None: out.append(PortfolioConstraintCheck(code=self.CHECK_MAX_GROSS_EXPOSURE,status=PortfolioCheckStatus.UNKNOWN,limit_value=c.max_portfolio_gross_exposure_pct,unit="PCT_NAV",reason="Max portfolio gross exposure cannot be evaluated because target_exposure_eur is UNKNOWN."))
        elif nav is None: out.append(PortfolioConstraintCheck(code=self.CHECK_MAX_GROSS_EXPOSURE,status=PortfolioCheckStatus.UNKNOWN,limit_value=c.max_portfolio_gross_exposure_pct,unit="PCT_NAV",reason="Max portfolio gross exposure cannot be evaluated because canonical AccountState NAV/equity is UNKNOWN."))
        else:
            cur=before.gross_exposure_eur/nav*100.0; proj=(before.gross_exposure_eur+target)/nav*100.0; ok=proj<=c.max_portfolio_gross_exposure_pct
            out.append(PortfolioConstraintCheck(code=self.CHECK_MAX_GROSS_EXPOSURE,status=PortfolioCheckStatus.PASS if ok else PortfolioCheckStatus.FAIL,current_value=cur,projected_value=proj,limit_value=c.max_portfolio_gross_exposure_pct,unit="PCT_NAV",reason=None if ok else "Max portfolio gross exposure constraint exceeded."))

        out.append(self._unknown_or_na(self.CHECK_MAX_CIO_DEPLOYABLE,c.max_cio_deployable_pct,"PCT_DEPLOYABLE_CAPITAL","Max CIO deployable % requires exact capital/collateral from PositionSizer and is UNKNOWN pre-instrument."))
        if c.max_portfolio_beta is None:
            out.append(self._na(self.CHECK_MAX_PORTFOLIO_BETA, "BETA"))
        elif marginal_risk_context is None:
            out.append(PortfolioConstraintCheck(
                code=self.CHECK_MAX_PORTFOLIO_BETA,
                status=PortfolioCheckStatus.UNKNOWN,
                limit_value=c.max_portfolio_beta,
                unit="BETA",
                reason="Projected portfolio beta requires marginal-risk analysis and is currently UNKNOWN.",
            ))
        else:
            current_beta = marginal_risk_context.beta.before
            projected_beta = marginal_risk_context.beta.after
            ok = projected_beta <= c.max_portfolio_beta
            out.append(PortfolioConstraintCheck(
                code=self.CHECK_MAX_PORTFOLIO_BETA,
                status=PortfolioCheckStatus.PASS if ok else PortfolioCheckStatus.FAIL,
                current_value=current_beta,
                projected_value=projected_beta,
                limit_value=c.max_portfolio_beta,
                unit="BETA",
                reason=None if ok else "Max portfolio beta constraint exceeded.",
            ))
        if c.max_var_95_1d_eur is None:
            out.append(self._na(self.CHECK_MAX_VAR_95_1D, "EUR"))
        elif marginal_risk_context is None:
            out.append(PortfolioConstraintCheck(
                code=self.CHECK_MAX_VAR_95_1D,
                status=PortfolioCheckStatus.UNKNOWN,
                limit_value=c.max_var_95_1d_eur,
                unit="EUR",
                reason="Projected portfolio VaR requires marginal-risk analysis and is currently UNKNOWN.",
            ))
        else:
            current_var = marginal_risk_context.var_95_1d_eur.before
            projected_var = marginal_risk_context.var_95_1d_eur.after
            ok = projected_var <= c.max_var_95_1d_eur
            out.append(PortfolioConstraintCheck(
                code=self.CHECK_MAX_VAR_95_1D,
                status=PortfolioCheckStatus.PASS if ok else PortfolioCheckStatus.FAIL,
                current_value=current_var,
                projected_value=projected_var,
                limit_value=c.max_var_95_1d_eur,
                unit="EUR",
                reason=None if ok else "Max portfolio VaR constraint exceeded.",
            ))
        out.append(self._unknown_or_na(self.CHECK_MIN_CASH_RESERVE_EUR,c.min_cash_reserve_eur,"EUR","Post-trade EUR cash reserve requires exact funding semantics and is UNKNOWN pre-instrument."))
        out.append(self._unknown_or_na(self.CHECK_MIN_CASH_RESERVE_USD,c.min_cash_reserve_usd,"USD","Post-trade USD cash reserve requires exact funding semantics and is UNKNOWN pre-instrument."))
        return out

    def _derive_decision(self, checks, warnings, scoring_result=None):
        statuses = [x.status for x in checks]

        # 1. Known hard failures always dominate score/coverage.
        if PortfolioCheckStatus.FAIL in statuses:
            return PortfolioFitDecision.REJECT

        # Preserve legacy PF-1A semantics when no scoring engine is wired.
        if scoring_result is None:
            has_unknown = PortfolioCheckStatus.UNKNOWN in statuses
            has_pass = PortfolioCheckStatus.PASS in statuses
            if has_unknown:
                return (
                    PortfolioFitDecision.PASS_WITH_WARNING
                    if has_pass else PortfolioFitDecision.UNKNOWN
                )
            if warnings:
                return PortfolioFitDecision.PASS_WITH_WARNING
            return PortfolioFitDecision.PASS

        # 2. PF-1D.1 minimum scoring coverage gate.
        if scoring_result.portfolio_fit_score is None:
            return PortfolioFitDecision.UNKNOWN

        score = scoring_result.portfolio_fit_score

        # 3. Materially adverse portfolio fit.
        if score < 35.0:
            return PortfolioFitDecision.REJECT

        # 4. Configured-but-unevaluable constraints prevent a clean PASS.
        if PortfolioCheckStatus.UNKNOWN in statuses:
            return PortfolioFitDecision.PASS_WITH_WARNING

        # 5. Insufficient analytical coverage prevents a clean PASS.
        analytical = scoring_result.analytical_coverage_pct
        if analytical is not None and analytical < 70.0:
            return PortfolioFitDecision.PASS_WITH_WARNING

        # Existing deterministic analytical warnings also prevent clean PASS.
        if warnings:
            return PortfolioFitDecision.PASS_WITH_WARNING

        # 6/7. Strong fit passes; otherwise the trade remains viable with warning.
        if score >= 70.0:
            return PortfolioFitDecision.PASS
        return PortfolioFitDecision.PASS_WITH_WARNING


    def _build_rationale(self, decision, checks, scoring_result=None):
        failed = sum(x.status == PortfolioCheckStatus.FAIL for x in checks)
        unknown = sum(x.status == PortfolioCheckStatus.UNKNOWN for x in checks)

        if scoring_result is None:
            if decision == PortfolioFitDecision.REJECT:
                return f"Portfolio fit rejected by {failed} known deterministic constraint failure(s)."
            if decision == PortfolioFitDecision.UNKNOWN:
                return f"Portfolio fit is unknown because no decisive pre-instrument check can be completed; {unknown} check(s) remain unknown."
            if decision == PortfolioFitDecision.PASS_WITH_WARNING:
                return f"No known PF-1A constraint failure was detected, but {unknown} check(s) remain unknown or analytical warnings are present."
            return "All configured PF-1A constraints that are applicable and knowable pre-instrument passed."

        score = scoring_result.portfolio_fit_score
        coverage = scoring_result.scoring_coverage_pct
        analytical = scoring_result.analytical_coverage_pct

        if failed:
            return (
                f"Portfolio fit rejected by {failed} known deterministic constraint "
                f"failure(s); scoring coverage={coverage:.2f}%."
            )
        if score is None:
            return (
                "Portfolio fit is UNKNOWN because deterministic scoring coverage "
                f"is {coverage:.2f}%, below the 70.00% minimum."
            )

        parts = [
            f"Deterministic portfolio fit score={score:.2f}/100",
            f"scoring coverage={coverage:.2f}%",
        ]
        if analytical is not None:
            parts.append(f"analytical coverage={analytical:.2f}%")
        parts.append(f"{unknown} configured check(s) remain UNKNOWN")

        if decision == PortfolioFitDecision.REJECT:
            parts.append("score is below the 35.00 rejection threshold")
        elif decision == PortfolioFitDecision.PASS:
            parts.append("score meets the 70.00 strong-fit threshold with no blocking uncertainty")
        else:
            parts.append("trade remains viable but does not qualify for an unqualified PASS")

        return "; ".join(parts) + "."


    def _new_assessment_id(self, ticker, now):
        return f"PFIT-{ticker.upper()}-{now:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
