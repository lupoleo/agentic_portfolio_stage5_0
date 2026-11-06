"""Account sources: the live IBKR account and the TEMPORARY simulation.

Production uses ``LiveIbkrAccountSource`` only. ``SimulatedFinecoUsdSource``
exists while Paolo's US stocks are being transferred from Fineco to IBKR: it
reads the real IBKR cash, account metrics and profile, and replaces the
(empty) position list with the Fineco USD stocks of a reviewed JSON fixture.
Real IBKR positions, if any, are kept; if one of them is a simulated stock
the transfer has arrived and the simulation refuses to run. Every reading it produces is marked ``SIMULATED_TEMPORARY`` and is stored in a
separate lineage, so it can never be mistaken for, or chained to, real state.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import Field

from .ibkr_client import ReadOnlyIbkrClient
from .models import (
    AccountMetrics,
    AccountReading,
    BrokerModel,
    BrokerPosition,
    PortfolioMode,
    StateOrigin,
)
from .normalize import balances_from_ibkr, metrics_from_ibkr, position_from_ibkr, profile_from

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AccountSource(Protocol):
    mode: PortfolioMode

    def read(self) -> AccountReading:
        ...


class LiveIbkrAccountSource:
    mode = PortfolioMode.LIVE

    def __init__(self, client: ReadOnlyIbkrClient, clock: Clock = utc_now) -> None:
        self._client = client
        self._clock = clock

    def read(self) -> AccountReading:
        observed_at = self._clock()
        metrics = metrics_from_ibkr(self._client.account_summary())
        balances = balances_from_ibkr(self._client.account_balances())
        positions = tuple(sorted((position_from_ibkr(p) for p in self._client.account_positions()), key=lambda p: p.conid))
        flow_type = self._client.flow_type()
        orders = tuple(self._client.account_orders())
        return AccountReading(
            observed_at=observed_at,
            mode=self.mode,
            origin=StateOrigin.REAL_IBKR,
            positions=positions,
            balances=balances,
            metrics=metrics,
            profile=profile_from(metrics, balances, flow_type),
            open_orders=orders,
            source_detail="ibkr-connector:live",
        )


# --- TEMPORARY simulation ---------------------------------------------------------------


class SimulationError(RuntimeError):
    pass


class SimulationExpiredError(SimulationError):
    pass


class SimulatedHolding(BrokerModel):
    conid: int = Field(gt=0)
    symbol: str
    isin: str
    description: str
    listing_exchange: str
    currency: str = Field(min_length=3, max_length=3)
    quantity: float = Field(gt=0)
    average_price: float = Field(gt=0)
    market_price_at_export: float = Field(gt=0)


class SimulationFixture(BrokerModel):
    fixture_id: str
    purpose: str
    valid_until: date
    source_file: str
    source_file_sha256: str = Field(min_length=64, max_length=64)
    source_exported_on: date
    holdings: tuple[SimulatedHolding, ...] = Field(min_length=1)

    @classmethod
    def load(cls, path: str | Path) -> "SimulationFixture":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


PriceLookup = Callable[[int], float | None]


class SimulatedFinecoUsdSource:
    """Real IBKR cash/metrics/profile + simulated positions (TEMPORARY)."""

    mode = PortfolioMode.SIMULATED_FINECO_USD

    def __init__(
        self,
        client: ReadOnlyIbkrClient,
        fixture: SimulationFixture,
        *,
        price_lookup: PriceLookup | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._client = client
        self._fixture = fixture
        self._price_lookup = price_lookup
        self._clock = clock

    def read(self) -> AccountReading:
        observed_at = self._clock()
        if observed_at.date() > self._fixture.valid_until:
            raise SimulationExpiredError(
                f"simulation {self._fixture.fixture_id} expired on {self._fixture.valid_until}; "
                "switch to LIVE mode or renew the fixture explicitly"
            )

        real_metrics = metrics_from_ibkr(self._client.account_summary())
        balances = balances_from_ibkr(self._client.account_balances())
        flow_type = self._client.flow_type()
        real_positions = [position_from_ibkr(p) for p in self._client.account_positions()]
        overlap = {p.conid for p in real_positions} & {h.conid for h in self._fixture.holdings}
        if overlap:
            raise SimulationError(
                f"conids {sorted(overlap)} are now real IBKR positions: the transfer has arrived, "
                "switch to LIVE mode instead of simulating them"
            )

        rates = {b.currency: b.exchange_rate_to_base for b in balances}
        positions: list[BrokerPosition] = list(real_positions)
        simulated_value_base = 0.0
        for h in self._fixture.holdings:
            price = self._price_lookup(h.conid) if self._price_lookup else None
            if price is None:
                price = h.market_price_at_export
            value = price * h.quantity
            rate = 1.0 if h.currency == real_metrics.base_currency else rates.get(h.currency)
            if rate is None:
                raise SimulationError(f"no IBKR exchange rate for {h.currency}; cannot value the simulation")
            simulated_value_base += value * rate
            positions.append(
                BrokerPosition(
                    conid=h.conid,
                    symbol=h.symbol,
                    description=h.description,
                    asset_class="STK",
                    currency=h.currency,
                    quantity=h.quantity,
                    average_price=h.average_price,
                    market_price=price,
                    market_value=value,
                    unrealized_pnl=(price - h.average_price) * h.quantity,
                    isin=h.isin,
                    origin=StateOrigin.SIMULATED_TEMPORARY,
                )
            )

        nlv = real_metrics.net_liquidation + simulated_value_base
        gross = (real_metrics.gross_position_value or 0.0) + simulated_value_base
        metrics = AccountMetrics(
            **{
                **real_metrics.model_dump(),
                "net_liquidation": nlv,
                "gross_position_value": gross,
                "leverage": gross / nlv if nlv > 0 else None,
            }
        )
        detail = (
            f"SIMULATION {self._fixture.fixture_id} (temporary, valid until {self._fixture.valid_until}); "
            f"real IBKR cash, metrics and {len(real_positions)} real position(s) + "
            f"{len(self._fixture.holdings)} simulated holding(s)"
        )
        return AccountReading(
            observed_at=observed_at,
            mode=self.mode,
            origin=StateOrigin.SIMULATED_TEMPORARY,
            positions=tuple(sorted(positions, key=lambda p: p.conid)),
            balances=balances,
            metrics=metrics,
            profile=profile_from(real_metrics, balances, flow_type),
            open_orders=tuple(self._client.account_orders()),
            source_detail=detail,
        )
