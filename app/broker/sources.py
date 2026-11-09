"""Account sources: the live IBKR account.

``LiveIbkrAccountSource`` reads the account through a read-only
``ReadOnlyIbkrClient``. In production the transport is
``GatewayAccountTransport``: the account summary, cash per currency,
positions and open orders read live from IB Gateway (Read-Only API). A
recorded transport replays the same responses in tests and offline runs.

The TEMPORARY Fineco-USD simulation (revision 3) was removed on 2026-11-09
(revision 6): every reading is real IBKR state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

from .ibkr_client import BrokerClientError, ReadOnlyIbkrClient
from .models import AccountReading, PortfolioMode, StateOrigin
from .normalize import balances_from_ibkr, metrics_from_ibkr, position_from_ibkr, profile_from

Clock = Callable[[], datetime]

# IB Gateway cannot see the connector's order-instruction flow; the assistant
# checks ``get_flow_type`` live before creating an instruction (§4.4).
GATEWAY_FLOW_TYPE = "NOT_VISIBLE_VIA_GATEWAY"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AccountSource(Protocol):
    mode: PortfolioMode

    def read(self) -> AccountReading:
        ...


class GatewayAccountTransport:
    """``IbkrToolTransport`` answering the account tools from one IB Gateway
    account snapshot (read once, on the first call)."""

    def __init__(self, session) -> None:
        self._session = session
        self._snapshot: dict[str, Any] | None = None

    @property
    def notes(self) -> list[str]:
        return list((self._snapshot or {}).get("notes") or [])

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if tool == "get_flow_type":
            return {"flow_type": GATEWAY_FLOW_TYPE}
        if self._snapshot is None:
            self._snapshot = self._session.account_snapshot()
        if tool not in self._snapshot:
            raise BrokerClientError(f"IB Gateway account snapshot has no {tool}")
        return self._snapshot[tool]


class LiveIbkrAccountSource:
    mode = PortfolioMode.LIVE

    def __init__(self, client: ReadOnlyIbkrClient, clock: Clock = utc_now,
                 source_detail: str = "ibkr-connector:live") -> None:
        self._client = client
        self._clock = clock
        self._detail = source_detail

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
            source_detail=self._detail,
        )


def gateway_account_source(session, clock: Clock = utc_now) -> LiveIbkrAccountSource:
    """The production account source: live IBKR account through IB Gateway."""

    return LiveIbkrAccountSource(ReadOnlyIbkrClient(GatewayAccountTransport(session)), clock=clock,
                                 source_detail="ib-gateway:read-only-api")
