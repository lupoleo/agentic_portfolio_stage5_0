from dataclasses import dataclass

import pandas as pd


@dataclass
class TechnicalAnalysis:
    """
    Technical analysis results for a financial instrument.

    Historical price-based indicators are calculated using
    adjusted Yahoo Finance data.
    """

    symbol: str

    current_price: float

    high_52w: float
    low_52w: float
    distance_from_high_52w_pct: float

    performance_1y_pct: float

    sma20: float
    sma50: float

    distance_from_sma20_pct: float
    distance_from_sma50_pct: float

    rsi14: float

    volatility_20d_pct: float

    average_volume_20d: float
    relative_volume: float

    trend: str


def calculate_sma(
    prices: pd.Series,
    window: int,
) -> float:
    """
    Calculate the Simple Moving Average (SMA).

    Example:
        window=20 -> SMA20
        window=50 -> SMA50
    """

    return float(
        prices.rolling(
            window=window
        ).mean().iloc[-1]
    )


def calculate_rsi(
    prices: pd.Series,
    period: int = 14,
) -> float:
    """
    Calculate RSI using Wilder-style exponential smoothing.

    RSI ranges from 0 to 100.

    Typical interpretation:
        RSI > 70 -> strong / traditionally overbought
        RSI < 30 -> weak / traditionally oversold

    These thresholds are descriptive and are not
    automatic buy/sell signals.
    """

    delta = prices.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_gain = average_gain.iloc[-1]
    avg_loss = average_loss.iloc[-1]

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    rsi = 100 - (
        100 / (1 + rs)
    )

    return float(rsi)


def calculate_volatility(
    prices: pd.Series,
    window: int = 20,
) -> float:
    """
    Calculate annualized historical volatility.

    Method:
        1. Calculate daily percentage returns.
        2. Calculate standard deviation over the last
           'window' trading sessions.
        3. Annualize assuming 252 trading sessions/year.

    The returned value is expressed as a percentage.
    """

    returns = (
        prices
        .pct_change()
        .dropna()
    )

    daily_volatility = (
        returns
        .tail(window)
        .std()
    )

    annualized_volatility = (
        daily_volatility
        * (252 ** 0.5)
    )

    return float(
        annualized_volatility * 100
    )


def calculate_relative_volume(
    volumes: pd.Series,
    window: int = 20,
) -> tuple[float, float]:
    """
    Calculate average volume and Relative Volume (RVOL).

    The current session is NOT included in the
    20-session reference average.

    RVOL =
        current volume /
        average volume of previous 20 sessions

    Example:
        RVOL = 2.0
        means current volume is twice the recent average.
    """

    if len(volumes) < window + 1:
        return 0.0, 0.0

    current_volume = volumes.iloc[-1]

    previous_volumes = volumes.iloc[
        -(window + 1):-1
    ]

    average_volume = (
        previous_volumes.mean()
    )

    if average_volume == 0:
        return (
            float(average_volume),
            0.0,
        )

    relative_volume = (
        current_volume
        / average_volume
    )

    return (
        float(average_volume),
        float(relative_volume),
    )


def classify_trend(
    current_price: float,
    sma20: float,
    sma50: float,
) -> str:
    """
    Classify the current technical trend.

    BULLISH:
        Price > SMA20 > SMA50

    BEARISH:
        Price < SMA20 < SMA50

    NEUTRAL:
        All other configurations.

    RSI, volume and volatility intentionally do not
    influence this classification.
    """

    if (
        current_price > sma20
        and sma20 > sma50
    ):
        return "BULLISH"

    if (
        current_price < sma20
        and sma20 < sma50
    ):
        return "BEARISH"

    return "NEUTRAL"


def analyze_technical(
    symbol: str,
    history: pd.DataFrame,
) -> TechnicalAnalysis:
    """
    Perform technical analysis on historical market data.

    Assumptions:
        - Historical data is adjusted data from Yahoo Finance.
        - One analytical year is defined as a maximum
          of 252 trading sessions.
        - SMA20 uses 20 trading sessions.
        - SMA50 uses 50 trading sessions.
        - RSI uses 14 trading sessions.
        - Volatility uses 20 trading sessions and is annualized.
        - Relative volume compares the latest session with
          the previous 20 sessions.
    """

    if history.empty:
        raise ValueError(
            f"No historical data for {symbol}"
        )

    if len(history) < 50:
        raise ValueError(
            f"Not enough historical data for {symbol}"
        )

    # ---------------------------------------------------------
    # Extract market series
    # ---------------------------------------------------------

    close = history["Close"].dropna()
    high = history["High"].dropna()
    low = history["Low"].dropna()
    volume = history["Volume"].dropna()

    if len(close) < 50:
        raise ValueError(
            f"Not enough valid closing prices for {symbol}"
        )

    # ---------------------------------------------------------
    # Standard analytical one-year window
    #
    # 252 trading sessions are conventionally used as
    # approximately one trading year.
    #
    # If fewer than 252 sessions are available, all
    # available observations are used.
    # ---------------------------------------------------------

    close_1y = close.tail(252)
    high_1y = high.tail(252)
    low_1y = low.tail(252)

    # ---------------------------------------------------------
    # Current price
    # ---------------------------------------------------------

    current_price = float(
        close.iloc[-1]
    )

    # ---------------------------------------------------------
    # 52-week / 1-year range
    #
    # Because the input history is adjusted Yahoo data,
    # these are adjusted historical highs and lows.
    # ---------------------------------------------------------

    high_52w = float(
        high_1y.max()
    )

    low_52w = float(
        low_1y.min()
    )

    distance_from_high_52w_pct = (
        (current_price / high_52w) - 1
    ) * 100

    # ---------------------------------------------------------
    # 1-year performance
    #
    # Uses the first available adjusted closing price
    # inside the maximum 252-session window.
    # ---------------------------------------------------------

    first_price_1y = float(
        close_1y.iloc[0]
    )

    performance_1y_pct = (
        (current_price / first_price_1y) - 1
    ) * 100

    # ---------------------------------------------------------
    # Moving averages
    # ---------------------------------------------------------

    sma20 = calculate_sma(
        close,
        20,
    )

    sma50 = calculate_sma(
        close,
        50,
    )

    # ---------------------------------------------------------
    # Distance from moving averages
    # ---------------------------------------------------------

    distance_from_sma20_pct = (
        (current_price / sma20) - 1
    ) * 100

    distance_from_sma50_pct = (
        (current_price / sma50) - 1
    ) * 100

    # ---------------------------------------------------------
    # RSI
    # ---------------------------------------------------------

    rsi14 = calculate_rsi(
        close,
        14,
    )

    # ---------------------------------------------------------
    # Volatility
    # ---------------------------------------------------------

    volatility_20d_pct = (
        calculate_volatility(
            close,
            20,
        )
    )

    # ---------------------------------------------------------
    # Volume analysis
    # ---------------------------------------------------------

    (
        average_volume_20d,
        relative_volume,
    ) = calculate_relative_volume(
        volume,
        20,
    )

    # ---------------------------------------------------------
    # Trend classification
    # ---------------------------------------------------------

    trend = classify_trend(
        current_price,
        sma20,
        sma50,
    )

    # ---------------------------------------------------------
    # Build result
    # ---------------------------------------------------------

    return TechnicalAnalysis(
        symbol=symbol,

        current_price=current_price,

        high_52w=high_52w,
        low_52w=low_52w,

        distance_from_high_52w_pct=(
            distance_from_high_52w_pct
        ),

        performance_1y_pct=(
            performance_1y_pct
        ),

        sma20=sma20,
        sma50=sma50,

        distance_from_sma20_pct=(
            distance_from_sma20_pct
        ),

        distance_from_sma50_pct=(
            distance_from_sma50_pct
        ),

        rsi14=rsi14,

        volatility_20d_pct=(
            volatility_20d_pct
        ),

        average_volume_20d=(
            average_volume_20d
        ),

        relative_volume=(
            relative_volume
        ),

        trend=trend,
    )


# -------------------------------------------------------------
# Standalone test
# -------------------------------------------------------------

if __name__ == "__main__":

    from app.market_data.yahoo_provider import (
        get_price_history,
    )

    history = get_price_history(
        "NVDA",
        period="1y",
    )

    analysis = analyze_technical(
        "NVDA",
        history,
    )

    print(
        "\n=== NVDA TECHNICAL ANALYSIS ==="
    )

    print(
        f"Current price:        "
        f"${analysis.current_price:.2f}"
    )

    print(
        f"52-week high:         "
        f"${analysis.high_52w:.2f}"
    )

    print(
        f"52-week low:          "
        f"${analysis.low_52w:.2f}"
    )

    print(
        f"Distance from high:   "
        f"{analysis.distance_from_high_52w_pct:+.2f}%"
    )

    print(
        f"1Y performance:       "
        f"{analysis.performance_1y_pct:+.2f}%"
    )

    print(
        f"SMA20:                "
        f"${analysis.sma20:.2f}"
    )

    print(
        f"SMA50:                "
        f"${analysis.sma50:.2f}"
    )

    print(
        f"Distance SMA20:       "
        f"{analysis.distance_from_sma20_pct:+.2f}%"
    )

    print(
        f"Distance SMA50:       "
        f"{analysis.distance_from_sma50_pct:+.2f}%"
    )

    print(
        f"RSI14:                "
        f"{analysis.rsi14:.2f}"
    )

    print(
        f"20d volatility:       "
        f"{analysis.volatility_20d_pct:.2f}%"
    )

    print(
        f"20d avg volume:       "
        f"{analysis.average_volume_20d:,.0f}"
    )

    print(
        f"Relative volume:      "
        f"{analysis.relative_volume:.2f}x"
    )

    print(
        f"Trend:                "
        f"{analysis.trend}"
    )