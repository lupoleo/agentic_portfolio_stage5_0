import pandas as pd
import pytest

from app.ai.canonical_technical import (
    _period_return_pct,
    canonical_technical_from_history,
)


def _history(rows=80):
    index = pd.date_range("2026-01-01", periods=rows, freq="B")
    close = pd.Series(
        [100.0 + i for i in range(rows)],
        index=index,
    )
    return pd.DataFrame(
        {
            "Close": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Volume": [1_000_000 + i * 1000 for i in range(rows)],
        },
        index=index,
    )


def test_period_return_uses_exact_session_lookback():
    history = _history()
    close = history["Close"]

    expected = ((close.iloc[-1] / close.iloc[-6]) - 1.0) * 100.0
    assert _period_return_pct(close, 5) == pytest.approx(expected)


def test_canonical_input_is_built_from_stage2_technical_analysis():
    result = canonical_technical_from_history("path", _history())

    assert result.ticker == "PATH"
    assert result.current_price > 0
    assert result.sma20 > 0
    assert result.sma50 > 0
    assert 0 <= result.rsi14 <= 100
    assert result.rvol > 0
    assert result.source == "STAGE2_YAHOO_TECHNICAL"
    assert result.return_1d_pct is not None
    assert result.return_5d_pct is not None
    assert result.return_20d_pct is not None
