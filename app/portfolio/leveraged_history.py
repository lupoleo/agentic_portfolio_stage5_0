from __future__ import annotations

import pandas as pd


def leveraged_proxy_factor_id(isin: str) -> str:
    normalized = str(isin).strip().upper()
    if not normalized:
        raise ValueError("ISIN is required for a unique leveraged proxy factor")
    return f"PROXY:{normalized}"


def build_leveraged_proxy_history(
    underlying_history: pd.DataFrame,
    leverage_multiplier: float,
) -> pd.DataFrame:
    """Build an auditable return proxy; direction is deliberately excluded."""
    if leverage_multiplier <= 0:
        raise ValueError("leverage_multiplier must be positive")
    if underlying_history is None or underlying_history.empty:
        raise ValueError("Underlying history is empty")
    close_name = "Adj Close" if "Adj Close" in underlying_history.columns else "Close"
    close = pd.to_numeric(underlying_history[close_name], errors="coerce").dropna()
    returns = close.pct_change(fill_method=None).dropna() * leverage_multiplier
    if returns.empty:
        raise ValueError("Underlying history has insufficient valid returns")
    if (returns <= -1.0).any():
        raise ValueError("Leveraged proxy return reached or crossed total loss")
    synthetic = (1.0 + returns).cumprod() * 100.0
    result = pd.DataFrame(index=synthetic.index)
    for column in ("Open", "High", "Low", "Close", "Adj Close"):
        result[column] = synthetic
    result["Volume"] = 0.0
    result.attrs.update(
        history_method="LEVERAGED_PROXY",
        leverage_multiplier=float(leverage_multiplier),
        underlying_symbol=getattr(underlying_history, "attrs", {}).get("symbol"),
        direction_embedded=False,
    )
    return result
