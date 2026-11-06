from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.cio.models import (
    AccountState,
    Currency,
    Direction,
    PortfolioRiskDelta,
    PortfolioRiskState,
    PortfolioSimulation,
    PortfolioSnapshot,
    TradeProposal,
)


class PortfolioRiskSimulator:
    """
    Stage 3 Portfolio Risk Simulator.

    V1 responsibilities:
        - validate snapshot/proposal consistency;
        - simulate LONG/SHORT gross/net exposure mechanics;
        - calculate basic position-weight effects;
        - apply constraints that can be evaluated deterministically;
        - preserve quantitative risk metrics that are not yet recomputed;
        - emit warnings when metrics cannot yet be recalculated.

    PF-1G.2 V2 behavior:
        - when a marginal-risk adapter is injected, portfolio volatility,
          beta, VaR, CVaR and analytical coverage are recomputed by the
          shared PortfolioMarginalRiskEngine;
        - concentration and factor exposure remain unchanged for now;
        - when no adapter is injected, legacy V1 behavior is preserved
          for backward compatibility until PF-1G.5 composition wiring.
    """

    def __init__(
        self,
        *,
        marginal_risk_adapter=None,
    ) -> None:
        self.marginal_risk_adapter = marginal_risk_adapter

    def simulate(
        self,
        *,
        snapshot: PortfolioSnapshot,
        proposal: TradeProposal,
        before: PortfolioRiskState,
        account_state: AccountState,
    ) -> PortfolioSimulation:

        self._validate_inputs(
            snapshot=snapshot,
            proposal=proposal,
            before=before,
        )

        warnings: list[str] = []
        violations: list[str] = []

        trade_exposure = (
            proposal.gross_exposure_eur
        )

        # =====================================================
        # Exposure mechanics
        # =====================================================

        if proposal.direction == Direction.LONG:

            long_after = (
                before.long_exposure_eur
                + trade_exposure
            )

            short_after = (
                before.short_exposure_eur
            )

        elif proposal.direction == Direction.SHORT:

            long_after = (
                before.long_exposure_eur
            )

            short_after = (
                before.short_exposure_eur
                + trade_exposure
            )

        else:

            raise ValueError(
                "Unsupported TradeProposal direction"
            )

        gross_after = (
            long_after
            + short_after
        )

        net_after = (
            long_after
            - short_after
        )

        # =====================================================
        # Quant metrics
        # =====================================================
        #
        # PF-1G.2 consumes the shared PF-1C marginal-risk engine when
        # the V2 adapter is configured. No quantitative formula is
        # duplicated here.
        #
        # Backward compatibility:
        # callers that do not inject the adapter retain the exact V1
        # placeholder semantics. This compatibility path is temporary
        # until service/CLI composition is upgraded in PF-1G.5.
        # =====================================================

        if self.marginal_risk_adapter is not None:

            marginal_risk = (
                self.marginal_risk_adapter.assess(
                    snapshot=snapshot,
                    proposal=proposal,
                )
            )

            volatility_after = (
                marginal_risk.volatility_pct.after
            )

            beta_after = (
                marginal_risk.beta.after
            )

            var_after = (
                marginal_risk.var_95_1d_eur.after
            )

            cvar_after = (
                marginal_risk.cvar_95_1d_eur.after
            )

            coverage_after = (
                marginal_risk.analytical_coverage_after_pct
            )

        else:

            volatility_after = (
                before.portfolio_volatility_pct
            )

            beta_after = (
                before.portfolio_beta
            )

            var_after = (
                before.var_95_1d_eur
            )

            cvar_after = (
                before.cvar_95_1d_eur
            )

            coverage_after = (
                before.analytical_coverage_pct
            )

            warnings.append(
                "Portfolio volatility AFTER is not yet "
                "recomputed by PortfolioRiskSimulator V1."
            )

            warnings.append(
                "Portfolio beta AFTER is not yet "
                "recomputed by PortfolioRiskSimulator V1."
            )

            warnings.append(
                "VaR and CVaR AFTER are not yet "
                "recomputed by PortfolioRiskSimulator V1."
            )

        # PF-1G.4 concentration: consume shared-engine Stage 2.1
        # gross-weight metrics. LONG and SHORT intentionally do not net.
        if self.marginal_risk_adapter is not None:
            top5_after = getattr(
                marginal_risk,
                "top5_concentration_after_pct",
                None,
            )
            effective_positions_after = getattr(
                marginal_risk,
                "effective_positions_after",
                None,
            )
            if (
                top5_after is None
                or effective_positions_after is None
            ):
                # Compatibility boundary for synthetic/frozen adapters from
                # PF-1G.1/2/3. Canonical PF-1G.4 marginal-risk results always
                # provide these metrics, but older test doubles do not.
                #
                # This is explicit degradation, not silent fallback.
                top5_after = before.top5_concentration_pct
                effective_positions_after = before.effective_positions
                warnings.append(
                    "PF-1G.4 concentration metrics are unavailable from "
                    "the injected marginal-risk result; portfolio "
                    "concentration AFTER is not yet recomputed from "
                    "constituent-level positions for this legacy result; "
                    "persisted BEFORE concentration is retained for "
                    "compatibility."
                )
        else:
            top5_after = before.top5_concentration_pct
            effective_positions_after = before.effective_positions
            warnings.append(
                "Portfolio concentration AFTER is not yet "
                "recomputed from constituent-level positions."
            )

        if (
            coverage_after is not None
            and coverage_after < 100
        ):

            warnings.append(
                "Portfolio analytical coverage is "
                f"{coverage_after:.2f}%."
            )

        # =====================================================
        # Constraint checks
        # =====================================================

        constraints = (
            account_state.constraints
        )

        # -----------------------------------------------------
        # Max trade loss
        # -----------------------------------------------------

        if (
            constraints.max_trade_loss_eur
            is not None
        ):

            if (
                proposal.estimated_max_loss_eur
                is None
            ):

                warnings.append(
                    "Trade loss constraint cannot be fully "
                    "evaluated because estimated_max_loss_eur "
                    "is UNKNOWN."
                )

            elif (
                proposal.estimated_max_loss_eur
                > constraints.max_trade_loss_eur
            ):

                violations.append(
                    "Max trade loss constraint exceeded: "
                    f"€{proposal.estimated_max_loss_eur:,.2f} "
                    f"> €{constraints.max_trade_loss_eur:,.2f}."
                )

        # -----------------------------------------------------
        # Max position weight
        #
        # V1 interprets the new position gross weight against
        # the hypothetical AFTER gross exposure.
        # -----------------------------------------------------

        if (
            constraints.max_position_weight_pct
            is not None
            and gross_after > 0
        ):

            new_position_weight_pct = (
                trade_exposure
                / gross_after
                * 100.0
            )

            if (
                new_position_weight_pct
                > constraints.max_position_weight_pct
            ):

                violations.append(
                    "Max position weight constraint exceeded: "
                    f"{new_position_weight_pct:.2f}% "
                    f"> "
                    f"{constraints.max_position_weight_pct:.2f}%."
                )

        # -----------------------------------------------------
        # Max portfolio gross exposure %
        #
        # Evaluate hypothetical AFTER gross exposure against the
        # canonical account NAV / equity denominator when known.
        #
        # Backward compatibility:
        # older AccountState records may have no canonical NAV.
        # In that case the constraint remains UNKNOWN and a
        # warning is emitted rather than manufacturing a result.
        # -----------------------------------------------------

        if (
            constraints.max_portfolio_gross_exposure_pct
            is not None
        ):

            nav_eur = (
                account_state.canonical_nav_eur
            )

            if nav_eur is None:

                warnings.append(
                    "Max portfolio gross exposure % cannot yet be evaluated "
                    "because canonical account NAV/equity is not "
                    "available in AccountState."
                )

            else:

                gross_exposure_pct = (
                    gross_after
                    / nav_eur
                    * 100.0
                )

                if (
                    gross_exposure_pct
                    > constraints.max_portfolio_gross_exposure_pct
                ):

                    violations.append(
                        "Max portfolio gross exposure constraint exceeded: "
                        f"{gross_exposure_pct:.2f}% "
                        f"> "
                        f"{constraints.max_portfolio_gross_exposure_pct:.2f}% "
                        f"(gross exposure "
                        f"€{gross_after:,.2f} / "
                        f"NAV €{nav_eur:,.2f})."
                    )

        # -----------------------------------------------------
        # Max CIO deployable %
        #
        # PositionSizer is the authority for capital/collateral
        # requirement. TradeProposal persists that EUR requirement,
        # allowing the simulator to validate CIO deployment policy
        # without reconstructing broker funding or FX semantics.
        #
        # Backward compatibility: older persisted proposals may not
        # carry estimated_capital_required_eur. In that case the
        # result is UNKNOWN rather than inferred from gross exposure.
        # -----------------------------------------------------

        if constraints.max_cio_deployable_pct is not None:

            capital_required_eur = (
                proposal.estimated_capital_required_eur
            )

            if capital_required_eur is None:
                warnings.append(
                    "Max CIO deployable % cannot yet be evaluated "
                    "because TradeProposal does not persist "
                    "estimated_capital_required_eur."
                )
            else:
                eur_cash = self._find_cash(account_state, Currency.EUR)
                if eur_cash is None:
                    warnings.append(
                        "Max CIO deployable % cannot yet be evaluated "
                        "because EUR cash is not available in AccountState."
                    )
                else:
                    deployable_eur = max(0.0, eur_cash.available - eur_cash.reserve)
                    cio_deployment_cap_eur = (
                        deployable_eur
                        * constraints.max_cio_deployable_pct
                        / 100.0
                    )
                    if capital_required_eur > cio_deployment_cap_eur:
                        violations.append(
                            "Max CIO deployable constraint exceeded: "
                            f"€{capital_required_eur:,.2f} "
                            f"> €{cio_deployment_cap_eur:,.2f} "
                            f"({constraints.max_cio_deployable_pct:.2f}% "
                            f"of deployable EUR cash €{deployable_eur:,.2f})."
                        )

        # -----------------------------------------------------
        # Portfolio beta
        #
        # PF-1G.3: when the V2 marginal-risk adapter is configured,
        # beta_after is a real shared-engine projection and the
        # configured portfolio constraint becomes enforceable.
        #
        # Legacy V1 callers remain backward compatible until the
        # canonical adapter is wired by PF-1G.5.
        # -----------------------------------------------------

        if constraints.max_portfolio_beta is not None:

            if self.marginal_risk_adapter is None:
                warnings.append(
                    "Max portfolio beta cannot yet be enforced "
                    "because portfolio beta AFTER is not yet "
                    "recomputed."
                )

            elif beta_after > constraints.max_portfolio_beta:
                violations.append(
                    "Max portfolio beta constraint exceeded: "
                    f"{beta_after:.4f} > "
                    f"{constraints.max_portfolio_beta:.4f}."
                )

        # -----------------------------------------------------
        # VaR
        #
        # PF-1G.3 applies the configured limit to projected AFTER
        # VaR, never to BEFORE or DELTA.
        # -----------------------------------------------------

        if constraints.max_var_95_1d_eur is not None:

            if self.marginal_risk_adapter is None:
                warnings.append(
                    "Max VaR 95% 1D cannot yet be enforced "
                    "because VaR AFTER is not yet recomputed."
                )

            elif var_after > constraints.max_var_95_1d_eur:
                violations.append(
                    "Max VaR 95% 1D constraint exceeded: "
                    f"€{var_after:,.2f} > "
                    f"€{constraints.max_var_95_1d_eur:,.2f}."
                )

        # =====================================================
        # Cash projection
        # =====================================================

        (
            cash_after_eur,
            cash_after_usd,
        ) = self._project_cash(
            proposal=proposal,
            account_state=account_state,
        )

        # =====================================================
        # Build BEFORE / AFTER / DELTA
        # =====================================================

        after = PortfolioRiskState(
            gross_exposure_eur=(
                gross_after
            ),

            net_exposure_eur=(
                net_after
            ),

            long_exposure_eur=(
                long_after
            ),

            short_exposure_eur=(
                short_after
            ),

            portfolio_volatility_pct=(
                volatility_after
            ),

            portfolio_beta=(
                beta_after
            ),

            var_95_1d_eur=(
                var_after
            ),

            cvar_95_1d_eur=(
                cvar_after
            ),

            top5_concentration_pct=(
                top5_after
            ),

            effective_positions=(
                effective_positions_after
            ),

            analytical_coverage_pct=(
                coverage_after
            ),
        )

        delta = PortfolioRiskDelta(
            gross_exposure_eur=(
                after.gross_exposure_eur
                - before.gross_exposure_eur
            ),

            net_exposure_eur=(
                after.net_exposure_eur
                - before.net_exposure_eur
            ),

            long_exposure_eur=(
                after.long_exposure_eur
                - before.long_exposure_eur
            ),

            short_exposure_eur=(
                after.short_exposure_eur
                - before.short_exposure_eur
            ),

            portfolio_volatility_pct=(
                after.portfolio_volatility_pct
                - before.portfolio_volatility_pct
            ),

            portfolio_beta=(
                after.portfolio_beta
                - before.portfolio_beta
            ),

            var_95_1d_eur=(
                after.var_95_1d_eur
                - before.var_95_1d_eur
            ),

            cvar_95_1d_eur=(
                after.cvar_95_1d_eur
                - before.cvar_95_1d_eur
            ),

            top5_concentration_pct=(
                after.top5_concentration_pct
                - before.top5_concentration_pct
            ),

            effective_positions=(
                after.effective_positions
                - before.effective_positions
            ),

            analytical_coverage_pct=(
                (
                    after.analytical_coverage_pct
                    - before.analytical_coverage_pct
                )
                if (
                    after.analytical_coverage_pct
                    is not None
                    and before.analytical_coverage_pct
                    is not None
                )
                else None
            ),
        )

        constraints_passed = (
            len(violations) == 0
        )

        now = datetime.now(
            timezone.utc
        )

        return PortfolioSimulation(
            simulation_id=(
                self._new_simulation_id(
                    proposal.ticker,
                    now,
                )
            ),

            snapshot_id=(
                snapshot.snapshot_id
            ),

            proposal_id=(
                proposal.proposal_id
            ),

            created_at=now,

            before=before,

            after=after,

            delta=delta,

            cash_after_eur=(
                cash_after_eur
            ),

            cash_after_usd=(
                cash_after_usd
            ),

            constraints_passed=(
                constraints_passed
            ),

            violated_constraints=(
                violations
            ),

            warnings=(
                warnings
            ),
        )

    # =========================================================
    # Validation
    # =========================================================

    def _validate_inputs(
        self,
        *,
        snapshot: PortfolioSnapshot,
        proposal: TradeProposal,
        before: PortfolioRiskState,
    ) -> None:

        if (
            proposal.snapshot_id
            != snapshot.snapshot_id
        ):

            raise ValueError(
                "TradeProposal snapshot_id does not match "
                "PortfolioSnapshot"
            )

        tolerance = 0.01

        if (
            abs(
                before.gross_exposure_eur
                - snapshot.gross_exposure_eur
            )
            > tolerance
        ):

            raise ValueError(
                "PortfolioRiskState BEFORE gross exposure "
                "does not match PortfolioSnapshot gross exposure"
            )

        if (
            abs(
                before.net_exposure_eur
                - snapshot.net_exposure_eur
            )
            > tolerance
        ):

            raise ValueError(
                "PortfolioRiskState BEFORE net exposure "
                "does not match PortfolioSnapshot net exposure"
            )

        expected_gross = (
            before.long_exposure_eur
            + before.short_exposure_eur
        )

        if (
            abs(
                expected_gross
                - before.gross_exposure_eur
            )
            > tolerance
        ):

            raise ValueError(
                "PortfolioRiskState BEFORE gross exposure "
                "is inconsistent with long + short exposure"
            )

        expected_net = (
            before.long_exposure_eur
            - before.short_exposure_eur
        )

        if (
            abs(
                expected_net
                - before.net_exposure_eur
            )
            > tolerance
        ):

            raise ValueError(
                "PortfolioRiskState BEFORE net exposure "
                "is inconsistent with long - short exposure"
            )

    # =========================================================
    # Cash projection
    # =========================================================

    def _project_cash(
        self,
        *,
        proposal: TradeProposal,
        account_state: AccountState,
    ) -> tuple[
        float | None,
        float | None,
    ]:
        """
        Preliminary cash projection.

        V1 deliberately avoids pretending to model Fineco's full
        multicurrency settlement behavior.

        BUY
            If instrument currency cash is known, subtract the gross
            trade value in that currency.

        SELL_SHORT
            No quote-currency cash deduction is applied in V1 because
            direct short-sale collateral is handled separately by the
            PositionSizer.

        This is an informational projection only.
        """

        eur_cash = self._find_cash(
            account_state,
            Currency.EUR,
        )

        usd_cash = self._find_cash(
            account_state,
            Currency.USD,
        )

        cash_after_eur = (
            eur_cash.available
            if eur_cash is not None
            else None
        )

        cash_after_usd = (
            usd_cash.available
            if usd_cash is not None
            else None
        )

        if (
            proposal.execution_side is None
        ):

            return (
                cash_after_eur,
                cash_after_usd,
            )

        if (
            proposal.execution_side.value
            == "BUY"
        ):

            # TradeProposal now persists fx_to_eur, but V1 still
            # deliberately avoids modelling Fineco's complete
            # multicurrency settlement mechanics. EUR-denominated
            # BUYs remain the only deterministic cash deduction here.

            if (
                proposal.currency
                == Currency.EUR
                and cash_after_eur
                is not None
            ):

                cash_after_eur = max(
                    0.0,
                    cash_after_eur
                    - proposal.gross_exposure_eur
                )

        return (
            cash_after_eur,
            cash_after_usd,
        )

    def _find_cash(
        self,
        account_state: AccountState,
        currency: Currency,
    ):
        for item in account_state.cash:

            if item.currency == currency:
                return item

        return None

    # =========================================================
    # IDs
    # =========================================================

    def _new_simulation_id(
        self,
        ticker: str,
        now: datetime,
    ) -> str:

        return (
            f"SIM-"
            f"{ticker.upper()}-"
            f"{now:%Y%m%d-%H%M%S}-"
            f"{uuid4().hex[:6]}"
        )