from __future__ import annotations

from datetime import datetime, timezone
from math import floor
from uuid import uuid4

from app.cio.models import (
    AccountState,
    Currency,
    Direction,
    ExecutionSide,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentType,
    PositionSizingResult,
    TradeOpportunity,
)


class PositionSizer:
    """
    Deterministic preliminary position sizing.

    The PositionSizer does NOT:
      - choose the instrument;
      - choose entry/stop levels;
      - simulate portfolio VaR;
      - create the final TradeProposal.

    It receives an already selected instrument and determines a
    preliminary executable quantity subject to known risk/cash limits.
    """

    def size(
        self,
        *,
        opportunity: TradeOpportunity,
        instrument: FinecoInstrument,
        account_state: AccountState,
        reference_price: float,
        fx_to_eur: float,
        stop_price: float | None = None,
        requested_exposure_eur: float | None = None,
    ) -> PositionSizingResult:

        if reference_price <= 0:
            raise ValueError(
                "reference_price must be > 0"
            )

        if fx_to_eur <= 0:
            raise ValueError(
                "fx_to_eur must be > 0"
            )

        execution_side = (
            self._execution_side(
                opportunity,
                instrument,
            )
        )

        risk_budget = (
            self._risk_budget(
                opportunity,
                account_state,
            )
        )

        target_exposure = (
            requested_exposure_eur
            if requested_exposure_eur is not None
            else opportunity.target_exposure_eur
        )

        unit_value_eur = (
            reference_price
            * fx_to_eur
        )

        quantity_limits: list[float] = []

        # -----------------------------------------------------
        # Exposure-based sizing
        # -----------------------------------------------------

        if target_exposure is not None:

            if target_exposure <= 0:
                raise ValueError(
                    "target exposure must be > 0"
                )

            quantity_limits.append(
                target_exposure
                / unit_value_eur
            )

        # -----------------------------------------------------
        # Risk-budget sizing
        #
        # Stop is expressed in the tradable instrument's own
        # price, not in the underlying price.
        # -----------------------------------------------------

        estimated_loss_per_unit_eur = None

        if stop_price is not None:

            if stop_price <= 0:
                raise ValueError(
                    "stop_price must be > 0"
                )

            estimated_loss_per_unit_eur = (
                abs(
                    reference_price
                    - stop_price
                )
                * fx_to_eur
            )

            if estimated_loss_per_unit_eur == 0:
                raise ValueError(
                    "stop_price cannot equal "
                    "reference_price"
                )

            if risk_budget is not None:

                quantity_limits.append(
                    risk_budget
                    / estimated_loss_per_unit_eur
                )

        # -----------------------------------------------------
        # CIO deployable-capital sizing
        #
        # V1 semantics:
        #   final quantity must not require more capital than
        #   max_cio_deployable_pct of currently deployable cash.
        #
        # This is a sizing limit, not merely a post-sizing
        # validation. Therefore the CIO deployment policy can
        # reduce quantity below the risk-budget quantity.
        # -----------------------------------------------------

        cio_deployable_pct = (
            account_state.constraints.max_cio_deployable_pct
        )

        if cio_deployable_pct is not None:

            funding_currency = (
                Currency.EUR
                if (
                    execution_side == ExecutionSide.SELL_SHORT
                    and instrument.exposure_relationship
                    == ExposureRelationship.DIRECT
                    and instrument.instrument_type
                    == InstrumentType.ORDINARY
                )
                else (
                    instrument.margin_currency
                    or instrument.settlement_currency
                    or instrument.quote_currency
                )
            )

            funding_cash = self._find_cash(
                account_state,
                funding_currency,
            )

            if funding_cash is not None:

                deployable_funding = max(
                    0.0,
                    funding_cash.available
                    - funding_cash.reserve,
                )

                cio_funding_cap = (
                    deployable_funding
                    * cio_deployable_pct
                    / 100.0
                )

                unit_capital_required_eur, _ = (
                    self._capital_requirement(
                        execution_side,
                        instrument,
                        unit_value_eur,
                    )
                )

                if funding_currency == Currency.EUR:

                    unit_capital_required_funding = (
                        unit_capital_required_eur
                    )

                else:

                    unit_capital_required_funding = (
                        unit_capital_required_eur
                        / fx_to_eur
                    )

                if unit_capital_required_funding > 0:

                    quantity_limits.append(
                        cio_funding_cap
                        / unit_capital_required_funding
                    )

        # -----------------------------------------------------
        # At least one sizing constraint is required.
        # -----------------------------------------------------

        if not quantity_limits:

            raise ValueError(
                "Cannot size position: provide "
                "target_exposure_eur or a stop_price "
                "together with a configured risk budget"
            )

        raw_quantity = min(
            quantity_limits
        )

        # Stage 3.2 initial assumption:
        # Fineco operating quantities are whole units.
        quantity = floor(
            raw_quantity
        )

        if quantity <= 0:

            raise ValueError(
                "Calculated quantity is below one unit"
            )

        gross_exposure_eur = (
            quantity
            * unit_value_eur
        )

        estimated_max_loss_eur = None

        if estimated_loss_per_unit_eur is not None:

            estimated_max_loss_eur = (
                quantity
                * estimated_loss_per_unit_eur
            )

        (
            capital_required_eur,
            estimated_margin_eur,
        ) = self._capital_requirement(
            execution_side,
            instrument,
            gross_exposure_eur,
        )

        violations: list[str] = []

        # -----------------------------------------------------
        # Risk-budget validation
        # -----------------------------------------------------

        if (
            risk_budget is not None
            and estimated_max_loss_eur is not None
            and estimated_max_loss_eur
            > risk_budget + 1e-9
        ):

            violations.append(
                "Estimated maximum loss exceeds "
                "configured trade risk budget."
            )

        # -----------------------------------------------------
        # Funding / collateral validation
        # -----------------------------------------------------

        if (
            execution_side == ExecutionSide.SELL_SHORT
            and instrument.exposure_relationship
            == ExposureRelationship.DIRECT
            and instrument.instrument_type
            == InstrumentType.ORDINARY
        ):
            eur_cash = self._find_cash(
                account_state,
                Currency.EUR,
            )

            if eur_cash is None:

                violations.append(
                    "No EUR Account State cash entry exists "
                    "for preliminary direct-short collateral."
                )

            else:

                deployable_eur = (
                    eur_cash.available
                    - eur_cash.reserve
                )

                if (
                    capital_required_eur
                    > deployable_eur + 1e-9
                ):

                    violations.append(
                        "Insufficient deployable EUR collateral "
                        "for preliminary direct-short sizing."
                    )

                cio_deployable_pct = (
                    account_state.constraints.max_cio_deployable_pct
                )

                if cio_deployable_pct is not None:

                    cio_cap_eur = (
                        deployable_eur
                        * cio_deployable_pct
                        / 100.0
                    )

                    if (
                        capital_required_eur
                        > cio_cap_eur + 1e-9
                    ):

                        violations.append(
                            "CIO deployable capital limit exceeded "
                            "for preliminary direct-short sizing."
                        )

        else:

            funding_currency = (
                instrument.margin_currency
                or instrument.settlement_currency
                or instrument.quote_currency
            )

            funding_cash = self._find_cash(
                account_state,
                funding_currency,
            )

            if funding_cash is None:

                violations.append(
                    "No Account State cash entry exists for "
                    f"{funding_currency.value}."
                )

            else:

                deployable = (
                    funding_cash.available
                    - funding_cash.reserve
                )

                if funding_currency == Currency.EUR:

                    required_funding = (
                        capital_required_eur
                    )

                else:

                    required_funding = (
                        capital_required_eur
                        / fx_to_eur
                    )

                if required_funding > deployable + 1e-9:

                    violations.append(
                        "Insufficient deployable "
                        f"{funding_currency.value} cash."
                    )

                cio_deployable_pct = (
                    account_state.constraints.max_cio_deployable_pct
                )

                if cio_deployable_pct is not None:

                    cio_cap = (
                        deployable
                        * cio_deployable_pct
                        / 100.0
                    )

                    if required_funding > cio_cap + 1e-9:

                        violations.append(
                            "CIO deployable capital limit exceeded "
                            f"for {funding_currency.value} funding."
                        )

        now = datetime.now(
            timezone.utc
        )

        return PositionSizingResult(
            sizing_id=(
                f"SIZ-"
                f"{opportunity.ticker.upper()}-"
                f"{now:%Y%m%d-%H%M%S}-"
                f"{uuid4().hex[:6]}"
            ),
            opportunity_id=(
                opportunity.opportunity_id
            ),
            instrument_id=(
                instrument.instrument_id
            ),
            created_at=now,
            execution_side=(
                execution_side
            ),
            reference_price=(
                reference_price
            ),
            currency=(
                instrument.quote_currency
            ),
            fx_to_eur=(
                fx_to_eur
            ),
            stop_price=(
                stop_price
            ),
            quantity=(
                quantity
            ),
            gross_exposure_eur=(
                gross_exposure_eur
            ),
            estimated_capital_required_eur=(
                capital_required_eur
            ),
            estimated_margin_eur=(
                estimated_margin_eur
            ),
            estimated_max_loss_eur=(
                estimated_max_loss_eur
            ),
            risk_budget_eur=(
                risk_budget
            ),
            constraints_passed=(
                len(violations) == 0
            ),
            violated_constraints=(
                violations
            ),
            notes=(
                "Stage 3.2 deterministic sizing with CIO deployable-capital limit. "
                "Portfolio-level simulation not yet applied."
            ),
        )

    # =========================================================
    # Execution side
    # =========================================================

    def _execution_side(
        self,
        opportunity: TradeOpportunity,
        instrument: FinecoInstrument,
    ) -> ExecutionSide:

        if opportunity.direction == Direction.LONG:

            return ExecutionSide.BUY

        if opportunity.direction == Direction.SHORT:

            if (
                instrument.exposure_relationship
                == ExposureRelationship.INVERSE
            ):

                return ExecutionSide.BUY

            if (
                instrument.exposure_relationship
                == ExposureRelationship.DIRECT
            ):

                if instrument.short_available is not True:

                    raise ValueError(
                        "Direct SHORT implementation requires "
                        "short_available=True"
                    )

                return (
                    ExecutionSide.SELL_SHORT
                )

        raise ValueError(
            "Instrument cannot implement "
            "opportunity direction"
        )

    # =========================================================
    # Risk budget
    # =========================================================

    def _risk_budget(
        self,
        opportunity: TradeOpportunity,
        account_state: AccountState,
    ) -> float | None:

        values = [
            value
            for value in (
                opportunity.max_intended_loss_eur,
                account_state.constraints.max_trade_loss_eur,
            )
            if value is not None
        ]

        if not values:
            return None

        return min(
            values
        )

    # =========================================================
    # Capital / margin
    # =========================================================

    def _capital_requirement(
        self,
        execution_side: ExecutionSide,
        instrument: FinecoInstrument,
        gross_exposure_eur: float,
    ) -> tuple[float, float | None]:
        """
        Return preliminary capital/collateral requirement in EUR.

        Direct ordinary SELL_SHORT currently uses a conservative
        100% gross-exposure collateral proxy. Fineco-specific short
        margin and multicurrency funding will be modeled later.
        """

        if (
            execution_side == ExecutionSide.SELL_SHORT
            and instrument.exposure_relationship
            == ExposureRelationship.DIRECT
            and instrument.instrument_type
            == InstrumentType.ORDINARY
        ):
            return (
                gross_exposure_eur,
                None,
            )

        leveraged_broker_types = {
            InstrumentType.MARGIN,
            InstrumentType.CFD,
            InstrumentType.CFDC,
        }

        if (
            instrument.instrument_type
            in leveraged_broker_types
        ):

            if instrument.margin_pct is not None:

                margin = (
                    gross_exposure_eur
                    * instrument.margin_pct
                    / 100.0
                )

                return (
                    margin,
                    margin,
                )

            if (
                instrument.broker_leverage
                is not None
                and instrument.broker_leverage > 1
            ):

                margin = (
                    gross_exposure_eur
                    / instrument.broker_leverage
                )

                return (
                    margin,
                    margin,
                )

        return (
            gross_exposure_eur,
            None,
        )

    # =========================================================
    # Cash
    # =========================================================

    def _find_cash(
        self,
        account_state: AccountState,
        currency: Currency,
    ):

        for item in account_state.cash:

            if item.currency == currency:
                return item

        return None