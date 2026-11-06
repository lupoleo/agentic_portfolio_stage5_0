from dataclasses import dataclass

from app.analysis.technical import TechnicalAnalysis


@dataclass
class TechnicalScores:
    momentum_score: float
    risk_score: float
    position_alignment: str


# -------------------------------------------------------------
# MOMENTUM SCORE
# -------------------------------------------------------------

def calculate_momentum_score(
    technical: TechnicalAnalysis,
) -> float:
    """
    Momentum score from 0 to 100.

    Higher = stronger positive technical momentum.

    Components:
        Trend structure      30 points
        Price vs SMA20       20 points
        Price vs SMA50       20 points
        RSI14                20 points
        Relative volume      10 points
    """

    score = 0.0

    # ---------------------------------------------------------
    # 1. Trend structure: max 30
    # ---------------------------------------------------------

    if technical.trend == "BULLISH":
        score += 30

    elif technical.trend == "NEUTRAL":
        score += 15

    # BEARISH -> 0

    # ---------------------------------------------------------
    # 2. Distance from SMA20: max 20
    #
    # Moderate positive distance is rewarded.
    # Extremely extended prices are not rewarded further.
    # ---------------------------------------------------------

    d20 = technical.distance_from_sma20_pct

    if 0 <= d20 <= 5:
        score += 20

    elif 5 < d20 <= 10:
        score += 15

    elif 10 < d20 <= 15:
        score += 10

    elif 15 < d20 <= 20:
        score += 5

    # Negative or >20% -> 0

    # ---------------------------------------------------------
    # 3. Distance from SMA50: max 20
    # ---------------------------------------------------------

    d50 = technical.distance_from_sma50_pct

    if 0 <= d50 <= 10:
        score += 20

    elif 10 < d50 <= 20:
        score += 15

    elif 20 < d50 <= 30:
        score += 10

    elif 30 < d50 <= 40:
        score += 5

    # Negative or >40% -> 0

    # ---------------------------------------------------------
    # 4. RSI14: max 20
    #
    # We reward positive momentum but avoid treating
    # extreme RSI as automatically better.
    # ---------------------------------------------------------

    rsi = technical.rsi14

    if 50 <= rsi < 60:
        score += 15

    elif 60 <= rsi < 70:
        score += 20

    elif 70 <= rsi < 80:
        score += 12

    elif 40 <= rsi < 50:
        score += 8

    elif 30 <= rsi < 40:
        score += 4

    # RSI <30 or >=80 -> 0

    # ---------------------------------------------------------
    # 5. Relative Volume: max 10
    #
    # High participation supports momentum.
    # ---------------------------------------------------------

    rvol = technical.relative_volume

    if rvol >= 2.0:
        score += 10

    elif rvol >= 1.5:
        score += 8

    elif rvol >= 1.0:
        score += 6

    elif rvol >= 0.75:
        score += 4

    elif rvol >= 0.5:
        score += 2

    return round(
        min(max(score, 0), 100),
        1,
    )


# -------------------------------------------------------------
# RISK SCORE
# -------------------------------------------------------------

def calculate_risk_score(
    technical: TechnicalAnalysis,
) -> float:
    """
    Technical risk score from 0 to 100.

    Higher = higher technical risk.

    Components:
        Annualized volatility     60 points
        SMA20 extension           20 points
        RSI extremes              20 points

    This is NOT yet a complete portfolio risk model.

    It does not include:
        correlation
        beta
        position size
        leverage
        sector concentration
        VaR
        drawdown
    """

    score = 0.0

    # ---------------------------------------------------------
    # 1. Volatility: max 60
    # ---------------------------------------------------------

    volatility = technical.volatility_20d_pct

    if volatility < 15:
        score += 10

    elif volatility < 25:
        score += 20

    elif volatility < 40:
        score += 30

    elif volatility < 60:
        score += 40

    elif volatility < 80:
        score += 50

    else:
        score += 60

    # ---------------------------------------------------------
    # 2. Price extension from SMA20: max 20
    #
    # Absolute distance is used because extreme deviations
    # in either direction represent instability.
    # ---------------------------------------------------------

    extension = abs(
        technical.distance_from_sma20_pct
    )

    if extension < 3:
        score += 0

    elif extension < 7:
        score += 5

    elif extension < 12:
        score += 10

    elif extension < 20:
        score += 15

    else:
        score += 20

    # ---------------------------------------------------------
    # 3. RSI extremes: max 20
    # ---------------------------------------------------------

    rsi = technical.rsi14

    if 40 <= rsi <= 60:
        score += 0

    elif 30 <= rsi < 40 or 60 < rsi <= 70:
        score += 5

    elif 20 <= rsi < 30 or 70 < rsi <= 80:
        score += 10

    else:
        score += 20

    return round(
        min(max(score, 0), 100),
        1,
    )


# -------------------------------------------------------------
# POSITION ALIGNMENT
# -------------------------------------------------------------

def calculate_position_alignment(
    direction: str,
    trend: str,
) -> str:
    """
    Determine whether the underlying technical trend
    supports the portfolio position.

    LONG + BULLISH   -> ALIGNED
    LONG + BEARISH   -> AGAINST

    SHORT + BEARISH  -> ALIGNED
    SHORT + BULLISH  -> AGAINST

    Any NEUTRAL trend -> NEUTRAL
    """

    if trend == "NEUTRAL":
        return "NEUTRAL"

    if direction == "LONG":

        if trend == "BULLISH":
            return "ALIGNED"

        if trend == "BEARISH":
            return "AGAINST"

    if direction == "SHORT":

        if trend == "BEARISH":
            return "ALIGNED"

        if trend == "BULLISH":
            return "AGAINST"

    return "NEUTRAL"


# -------------------------------------------------------------
# COMPLETE SCORING
# -------------------------------------------------------------

def calculate_scores(
    technical: TechnicalAnalysis,
    direction: str,
) -> TechnicalScores:

    momentum_score = (
        calculate_momentum_score(
            technical
        )
    )

    risk_score = (
        calculate_risk_score(
            technical
        )
    )

    position_alignment = (
        calculate_position_alignment(
            direction,
            technical.trend,
        )
    )

    return TechnicalScores(
        momentum_score=momentum_score,
        risk_score=risk_score,
        position_alignment=position_alignment,
    )