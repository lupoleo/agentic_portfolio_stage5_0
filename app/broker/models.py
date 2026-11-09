"""Canonical broker-state records for Stage 5.0 (architecture §5A, §5B)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BrokerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class PortfolioMode(str, Enum):
    """Which account state the run works on.

    ``LIVE`` is the only mode. ``SIMULATED_FINECO_USD`` (and the
    ``SIMULATED_TEMPORARY`` origin) belonged to the Fineco-USD simulation
    removed on 2026-11-09; they remain only so that versions persisted before
    then can still be read. No code produces them any more.
    """

    LIVE = "LIVE"
    SIMULATED_FINECO_USD = "SIMULATED_FINECO_USD"


class StateOrigin(str, Enum):
    REAL_IBKR = "REAL_IBKR"
    SIMULATED_TEMPORARY = "SIMULATED_TEMPORARY"


class BrokerPosition(BrokerModel):
    conid: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    description: str | None = None
    asset_class: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    quantity: float
    average_price: float | None = None
    market_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    isin: str | None = None
    origin: StateOrigin

    @field_validator("currency", "asset_class")
    @classmethod
    def upper(cls, value: str) -> str:
        return value.upper()


class CashBalance(BrokerModel):
    currency: str = Field(min_length=3, max_length=4)  # "BASE" is IBKR's base-currency row
    cash_balance: float
    settled_cash: float | None = None
    exchange_rate_to_base: float | None = Field(default=None, gt=0)


class AccountMetrics(BrokerModel):
    base_currency: str = Field(min_length=3, max_length=3)
    net_liquidation: float
    equity_with_loan_value: float | None = None
    total_cash_value: float | None = None
    gross_position_value: float | None = None
    available_funds: float | None = None
    buying_power: float | None = None
    initial_margin: float | None = None
    maintenance_margin: float | None = None
    excess_liquidity: float | None = None
    leverage: float | None = None


class AccountProfile(BrokerModel):
    """Account configuration (architecture §5B.1).

    Trading permissions and the IBKR legal entity / country are not exposed
    by the connector (gap G11). They will be filled from the Client Portal Web
    API (approved, read-only); until then they stay ``None`` and the
    instrument-availability rule fails closed.
    """

    base_currency: str = Field(min_length=3, max_length=3)
    flow_type: str = Field(min_length=1)
    cash_currencies: tuple[str, ...]
    ibkr_entity: str | None = None
    country: str | None = None
    trading_permissions: tuple[str, ...] | None = None


class AccountReading(BrokerModel):
    """One normalized read of the account, before versioning."""

    observed_at: datetime
    mode: PortfolioMode
    origin: StateOrigin
    positions: tuple[BrokerPosition, ...]
    balances: tuple[CashBalance, ...]
    metrics: AccountMetrics
    profile: AccountProfile
    open_orders: tuple[dict, ...] = ()
    source_detail: str = Field(min_length=1)

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class ChangeType(str, Enum):
    INITIAL = "INITIAL"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"
    QUANTITY_CHANGED = "QUANTITY_CHANGED"
    CASH_CHANGED = "CASH_CHANGED"
    ACCOUNT_RISK_CHANGED = "ACCOUNT_RISK_CHANGED"
    OPEN_ORDERS_CHANGED = "OPEN_ORDERS_CHANGED"
    ACCOUNT_PROFILE_CHANGED = "ACCOUNT_PROFILE_CHANGED"
    VALUATION_DRIFT = "VALUATION_DRIFT"


class StateChange(BrokerModel):
    change_type: ChangeType
    subject: str = Field(min_length=1)  # conid, currency, metric or "ACCOUNT"
    before: str | None = None
    after: str | None = None


class SyncOutcome(str, Enum):
    INITIAL = "INITIAL"
    CHANGED = "CHANGED"
    VALUATION_ONLY = "VALUATION_ONLY"
    NO_CHANGE = "NO_CHANGE"


class StateVersion(BrokerModel):
    """An immutable, fingerprinted version of the account state."""

    version_id: str = Field(min_length=1)
    parent_version_id: str | None = None
    mode: PortfolioMode
    origin: StateOrigin
    created_at: datetime
    structural_fingerprint: str = Field(min_length=64, max_length=64)
    account_fingerprint: str = Field(min_length=64, max_length=64)
    profile_fingerprint: str = Field(min_length=64, max_length=64)
    reading: AccountReading


class SyncResult(BrokerModel):
    sync_id: str
    observed_at: datetime
    outcome: SyncOutcome
    version: StateVersion
    changes: tuple[StateChange, ...]
    broker_orders_submitted: int = 0
    portfolio_mutations: int = 0
    automatic_executions: int = 0
