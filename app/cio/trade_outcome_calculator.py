from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.cio.models import (
    Currency,
    Direction,
    TradeOutcome,
)


@dataclass(frozen=True)
class TradeOutcomeCalculation:
    """
    Deterministic realized economics for one fully closed trade.
    """

    realized_pnl_eur: float
    realized_return_pct: float
    holding_days: float
    entry_slippage_pct: float | None


class TradeOutcomeCalculator:
    """
    Pure calculator for a fully closed TradeOutcome.

    No persistence, market-data lookup or broker interaction occurs here.

    EUR economics
    -------------
    EUR-denominated trade:
        entry_value_eur = entry_price * quantity
        exit_value_eur  = exit_price * quantity

    Non-EUR trade:
        entry_value_eur =
            entry_price * quantity * entry_fx_to_eur

        exit_value_eur =
            exit_price * quantity * exit_fx_to_eur

    LONG:
        gross_pnl_eur = exit_value_eur - entry_value_eur

    SHORT:
        gross_pnl_eur = entry_value_eur - exit_value_eur

    Net realized P/L subtracts both entry and exit commissions.

    realized_return_pct is net realized P/L divided by the absolute
    entry notional in EUR.

    entry_slippage_pct compares actual entry price with the approved
    reference price, expressed so positive slippage is adverse:
        LONG  -> (actual - reference) / reference
        SHORT -> (reference - actual) / reference
    """

    def calculate_closed(
        self,
        *,
        outcome: TradeOutcome,
        exit_datetime: datetime,
        exit_price: float,
        exit_quantity: float,
        exit_commission_eur: float = 0.0,
        exit_fx_to_eur: float | None = None,
    ) -> TradeOutcomeCalculation:

        self._validate(
            outcome=outcome,
            exit_datetime=exit_datetime,
            exit_price=exit_price,
            exit_quantity=exit_quantity,
            exit_commission_eur=exit_commission_eur,
            exit_fx_to_eur=exit_fx_to_eur,
        )

        quantity = outcome.entry_quantity

        if outcome.currency == Currency.EUR:
            entry_fx = 1.0
            exit_fx = 1.0
        else:
            # Guaranteed by _validate.
            entry_fx = outcome.entry_fx_to_eur
            exit_fx = exit_fx_to_eur

        entry_value_eur = (
            outcome.entry_price
            * quantity
            * entry_fx
        )

        exit_value_eur = (
            exit_price
            * quantity
            * exit_fx
        )

        if outcome.direction == Direction.LONG:
            gross_pnl_eur = (
                exit_value_eur
                - entry_value_eur
            )
        elif outcome.direction == Direction.SHORT:
            gross_pnl_eur = (
                entry_value_eur
                - exit_value_eur
            )
        else:
            raise ValueError(
                "Unsupported TradeOutcome direction: "
                f"{outcome.direction}"
            )

        entry_commission = (
            outcome.entry_commission_eur
            or 0.0
        )

        realized_pnl_eur = (
            gross_pnl_eur
            - entry_commission
            - exit_commission_eur
        )

        realized_return_pct = (
            realized_pnl_eur
            / abs(entry_value_eur)
            * 100.0
        )

        holding_days = (
            exit_datetime
            - outcome.entry_datetime
        ).total_seconds() / 86400.0

        entry_slippage_pct = (
            self._entry_slippage_pct(
                outcome
            )
        )

        return TradeOutcomeCalculation(
            realized_pnl_eur=realized_pnl_eur,
            realized_return_pct=realized_return_pct,
            holding_days=holding_days,
            entry_slippage_pct=entry_slippage_pct,
        )

    def _validate(
        self,
        *,
        outcome: TradeOutcome,
        exit_datetime: datetime,
        exit_price: float,
        exit_quantity: float,
        exit_commission_eur: float,
        exit_fx_to_eur: float | None,
    ) -> None:

        if outcome.status.value != "OPEN":
            raise ValueError(
                "TradeOutcomeCalculator requires "
                "status=OPEN"
            )

        if exit_datetime < outcome.entry_datetime:
            raise ValueError(
                "exit_datetime cannot be earlier "
                "than entry_datetime"
            )

        if exit_price <= 0:
            raise ValueError(
                "exit_price must be greater than zero"
            )

        if exit_quantity <= 0:
            raise ValueError(
                "exit_quantity must be greater than zero"
            )

        if exit_quantity != outcome.entry_quantity:
            raise ValueError(
                "Full-close V1 requires exit_quantity "
                "to equal entry_quantity"
            )

        if exit_commission_eur < 0:
            raise ValueError(
                "exit_commission_eur cannot be negative"
            )

        if outcome.currency != Currency.EUR:
            if outcome.entry_fx_to_eur is None:
                raise ValueError(
                    "entry_fx_to_eur is required "
                    "for non-EUR trade closure"
                )

            if exit_fx_to_eur is None:
                raise ValueError(
                    "exit_fx_to_eur is required "
                    "for non-EUR trade closure"
                )

            if exit_fx_to_eur <= 0:
                raise ValueError(
                    "exit_fx_to_eur must be greater "
                    "than zero"
                )

    def _entry_slippage_pct(
        self,
        outcome: TradeOutcome,
    ) -> float | None:

        reference = (
            outcome.planned_reference_price
        )

        if reference is None:
            return None

        if outcome.direction == Direction.LONG:
            return (
                (
                    outcome.entry_price
                    - reference
                )
                / reference
                * 100.0
            )

        if outcome.direction == Direction.SHORT:
            return (
                (
                    reference
                    - outcome.entry_price
                )
                / reference
                * 100.0
            )

        raise ValueError(
            "Unsupported TradeOutcome direction: "
            f"{outcome.direction}"
        )
