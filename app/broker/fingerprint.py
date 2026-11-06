"""Deterministic fingerprints of an account reading (architecture §5A.3).

* structural: which instruments, how many, at what cost; cash per currency;
  open orders. Prices are deliberately excluded.
* account: margin, buying power, available funds, leverage (rounded so that
  sub-cent noise is not a change).
* profile: base currency, flow type, permissions, entity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import AccountReading


def _digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _r(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def structural_payload(reading: AccountReading) -> dict[str, Any]:
    return {
        "mode": reading.mode.value,
        "origin": reading.origin.value,
        "positions": sorted(
            [
                {
                    "conid": p.conid,
                    "quantity": _r(p.quantity, 6),
                    "average_price": _r(p.average_price, 6),
                    "currency": p.currency,
                    "asset_class": p.asset_class,
                }
                for p in reading.positions
            ],
            key=lambda item: item["conid"],
        ),
        "cash": [
            {"currency": b.currency, "cash": _r(b.cash_balance)}
            for b in reading.balances
            if b.currency != "BASE"
        ],
        "open_orders": sorted(json.dumps(o, sort_keys=True) for o in reading.open_orders),
    }


def account_payload(reading: AccountReading) -> dict[str, Any]:
    m = reading.metrics
    return {
        "available_funds": _r(m.available_funds),
        "buying_power": _r(m.buying_power),
        "initial_margin": _r(m.initial_margin),
        "maintenance_margin": _r(m.maintenance_margin),
        "excess_liquidity": _r(m.excess_liquidity),
        "leverage": _r(m.leverage, 4),
    }


def profile_payload(reading: AccountReading) -> dict[str, Any]:
    return reading.profile.model_dump(mode="json")


def structural_fingerprint(reading: AccountReading) -> str:
    return _digest(structural_payload(reading))


def account_fingerprint(reading: AccountReading) -> str:
    return _digest(account_payload(reading))


def profile_fingerprint(reading: AccountReading) -> str:
    return _digest(profile_payload(reading))
