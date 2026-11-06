from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import uuid4

import pandas as pd

from app.analysis.technical import (
    TechnicalAnalysis,
    analyze_technical,
)

from app.analysis.scoring import (
    TechnicalScores,
    calculate_scores,
)

from app.analysis.risk import (
    PortfolioRiskSummary,
    PortfolioCovarianceRiskSummary,
    PortfolioHistoricalTailRiskSummary,
    calculate_portfolio_risk,
    calculate_covariance_risk,
    calculate_historical_tail_risk,
)

from app.analysis.benchmark import (
    PortfolioBenchmarkRiskSummary,
    calculate_benchmark_risk,
)

from app.analysis.multifactor import (
    PortfolioMultiFactorSummary,
    calculate_multifactor_risk,
)

from app.market_data.yahoo_provider import (
    download_price_history,
    get_price_history,
)

from app.portfolio.fineco_importer import (
    load_fineco_positions,
)

from app.portfolio.models import (
    PortfolioPosition,
)

from app.portfolio.ticker_resolver import (
    resolve_yahoo_symbol,
)
from app.portfolio.leveraged_history import (
    build_leveraged_proxy_history,
    leveraged_proxy_factor_id,
)

from app.reports.portfolio_report import (
    export_portfolio_analysis_to_excel,
)

from app.cio.models import (
    PortfolioRiskState,
    PortfolioRiskStateRecord,
    PortfolioSnapshot,
)

from app.cio.storage import (
    Stage3Store,
)


# =============================================================
# Analysis windows
# =============================================================
#
# Yahoo/yfinance does not consistently expose a native "3y" period.
# We therefore download a wider 5-year history once and let each
# analytical layer use the window it needs:
#
#   Technical analysis: max 252 sessions (~1 year)
#   Risk engine:        max 756 sessions (~3 years)
#
# Extended risk estimates require at least ~2 years of common history.
# Younger instruments are excluded from the extended risk sample and
# reported through Risk Coverage rather than silently shortening the
# whole portfolio history.
#
MARKET_DATA_DOWNLOAD_PERIOD = "5y"
TECHNICAL_MAX_SESSIONS = 252
RISK_MAX_SESSIONS = 756
RISK_MIN_COMMON_OBSERVATIONS = 504
BENCHMARK_SYMBOL = "SPY"

# Stage 2.5 factor proxies. These are configurable Yahoo symbols,
# not a claim that these ETFs are the only valid economic factors.
MULTI_FACTOR_SYMBOLS = {
    "US_MARKET": "SPY",
    "EUROPE": "VGK",
    "GOLD": "GLD",
    "TECH": "QQQ",
}

ASSET_NAME_WIDTH = 28

# Stage 3 persistence bridge.
STAGE3_DB_PATH = Path(
    "data/state/portfolio_cio.db"
)
QUANT_ENGINE_VERSION = "2.5"


def short_asset_name(
    name: str,
    width: int = ASSET_NAME_WIDTH,
) -> str:
    """Shorten long Fineco descriptions for console output."""

    name = str(name).strip()

    if len(name) <= width:
        return name

    return name[: width - 3] + "..."


@dataclass
class AnalyzedPosition:
    """
    Fineco portfolio position enriched with market data,
    technical analysis and technical scoring.
    """

    position: PortfolioPosition
    yahoo_symbol: str

    weight_pct: float
    price_gap_pct: float

    technical: TechnicalAnalysis
    scores: TechnicalScores
    history: pd.DataFrame

    market_data_symbol: str | None = None
    history_method: str = "DIRECT"
    leverage_multiplier: float = 1.0
    price_gap_comparable: bool = True
    instrument_reference_id: str | None = None


def analyze_portfolio(
    file_path: str | Path,
) -> list[AnalyzedPosition]:
    """
    Analyze all positions contained in a Fineco portfolio.

    Pipeline:

        Fineco Excel
            ->
        PortfolioPosition
            ->
        Yahoo ticker resolution
            ->
        Yahoo historical data
            ->
        Technical analysis
            ->
        Technical scoring
            ->
        AnalyzedPosition
    """

    # ---------------------------------------------------------
    # 1. Load Fineco portfolio
    # ---------------------------------------------------------

    positions = load_fineco_positions(
        file_path
    )

    if not positions:
        return []

    # ---------------------------------------------------------
    # 2. Calculate gross portfolio exposure
    #
    # LONG/SHORT NOTE:
    # Direction comes from quantity.  Gross exposure uses the
    # absolute market value so SHORT positions are included
    # correctly regardless of Fineco's accounting sign.
    # ---------------------------------------------------------

    gross_exposure = sum(
        abs(position.market_value_eur)
        for position in positions
        if position.direction != "FLAT"
    )

    if gross_exposure == 0:
        raise ValueError(
            "Portfolio gross exposure is zero"
        )

    # ---------------------------------------------------------
    # 3. Resolve Fineco symbols into Yahoo symbols
    # ---------------------------------------------------------

    symbol_by_position: dict[int, str] = {}

    for index, position in enumerate(positions):

        yahoo_symbol = resolve_yahoo_symbol(
            position
        )

        if yahoo_symbol is None:

            print(
                f"WARNING: Yahoo symbol not resolved for "
                f"{position.name}"
            )

            continue

        symbol_by_position[index] = (
            yahoo_symbol
        )

    # ---------------------------------------------------------
    # 4. Download Yahoo data in one batch
    # ---------------------------------------------------------

    yahoo_symbols = list(
        symbol_by_position.values()
    )

    market_data = download_price_history(
        yahoo_symbols,
        period=MARKET_DATA_DOWNLOAD_PERIOD,
    )

    # ---------------------------------------------------------
    # 5. Analyze EVERY portfolio position
    # ---------------------------------------------------------

    analyzed_positions: list[
        AnalyzedPosition
    ] = []

    for index, position in enumerate(positions):

        # -----------------------------------------------------
        # Find resolved Yahoo symbol
        # -----------------------------------------------------

        yahoo_symbol = (
            symbol_by_position.get(index)
        )

        if yahoo_symbol is None:
            continue

        # -----------------------------------------------------
        # Find downloaded market history
        # -----------------------------------------------------

        provider_history = market_data.get(
            yahoo_symbol
        )

        if provider_history is None or provider_history.empty:

            print(
                f"WARNING: No market data for "
                f"{position.name} "
                f"({yahoo_symbol})"
            )

            continue

        history = provider_history
        risk_factor_symbol = yahoo_symbol

        if position.market_data_method == "LEVERAGED_PROXY":
            try:
                history = build_leveraged_proxy_history(
                    provider_history,
                    position.leverage_multiplier,
                )
                risk_factor_symbol = leveraged_proxy_factor_id(position.isin)
            except ValueError as exc:
                print(
                    f"WARNING: Leveraged proxy skipped for {position.name} "
                    f"({yahoo_symbol}): {exc}"
                )
                continue

        # -----------------------------------------------------
        # Technical analysis
        #
        # A single instrument with missing or insufficient
        # Yahoo history must not abort the entire portfolio
        # analysis. Instruments that cannot support the
        # required technical indicators are excluded from
        # the analytical sample and explicitly reported.
        # -----------------------------------------------------

        try:
            technical = analyze_technical(
                risk_factor_symbol,
                history,
            )

        except ValueError as exc:

            print(
                f"WARNING: Technical analysis skipped for "
                f"{position.name} "
                f"({yahoo_symbol}): {exc}"
            )

            continue

        # -----------------------------------------------------
        # Technical scoring
        # -----------------------------------------------------

        scores = calculate_scores(
            technical,
            position.direction,
        )

        # -----------------------------------------------------
        # Portfolio weight
        # -----------------------------------------------------

        weight_pct = (
            abs(position.market_value_eur)
            / gross_exposure
            * 100
        )

        # -----------------------------------------------------
        # Fineco / Yahoo current-price validation
        # -----------------------------------------------------

        price_gap_comparable = position.history_is_real_product_price

        if price_gap_comparable and position.market_price != 0:

            price_gap_pct = (
                technical.current_price
                / position.market_price
                - 1
            ) * 100

        else:

            price_gap_pct = 0.0

        # -----------------------------------------------------
        # Build enriched position
        #
        # IMPORTANT:
        # This MUST remain inside the for loop.
        # -----------------------------------------------------

        analyzed_position = AnalyzedPosition(
            position=position,
            yahoo_symbol=risk_factor_symbol,
            weight_pct=weight_pct,
            price_gap_pct=price_gap_pct,
            technical=technical,
            scores=scores,
            history=history.copy(),
            market_data_symbol=yahoo_symbol,
            history_method=position.market_data_method,
            leverage_multiplier=position.leverage_multiplier,
            price_gap_comparable=price_gap_comparable,
            instrument_reference_id=position.instrument_reference_id,
        )

        # -----------------------------------------------------
        # Add position to final portfolio analysis
        #
        # IMPORTANT:
        # This MUST also remain inside the for loop.
        # -----------------------------------------------------

        analyzed_positions.append(
            analyzed_position
        )

    return analyzed_positions


def print_portfolio_analysis(
    analyzed_positions: list[AnalyzedPosition],
) -> None:
    """
    Print the main technical-analysis table.
    """

    print(
        "\n=== PORTFOLIO TECHNICAL ANALYSIS ===\n"
    )
    print(
        f"Technical window: max {TECHNICAL_MAX_SESSIONS} sessions (~1 year)\n"
    )

    header = (
        f"{'Symbol':<11}"
        f"{'Asset':<30}"
        f"{'Dir':<7}"
        f"{'Value EUR':>12}"
        f"{'Weight':>9}"
        f"{'P/L':>9}"
        f"{'PxGap':>9}"
        f"{'1Y':>9}"
        f"{'vsSMA20':>10}"
        f"{'RSI':>8}"
        f"{'Vol':>9}"
        f"{'RVOL':>8}"
        f"{'Trend':>10}"
    )

    print(header)
    print("-" * len(header))

    for item in analyzed_positions:

        position = item.position
        technical = item.technical

        print(
            f"{item.yahoo_symbol:<11}"
            f"{short_asset_name(position.name):<30}"
            f"{position.direction:<7}"
            f"{position.market_value_eur:>12,.0f}"
            f"{item.weight_pct:>8.2f}%"
            f"{position.pnl_percent:>+8.2f}%"
            f"{item.price_gap_pct:>+8.2f}%"
            f"{technical.performance_1y_pct:>+8.2f}%"
            f"{technical.distance_from_sma20_pct:>+9.2f}%"
            f"{technical.rsi14:>8.1f}"
            f"{technical.volatility_20d_pct:>8.1f}%"
            f"{technical.relative_volume:>8.2f}"
            f"{technical.trend:>10}"
        )


def print_scoring_analysis(
    analyzed_positions: list[AnalyzedPosition],
) -> None:
    """
    Print technical momentum, risk and position alignment.
    """

    print(
        "\n=== PORTFOLIO SCORING ===\n"
    )

    header = (
        f"{'Symbol':<12}"
        f"{'Asset':<30}"
        f"{'Dir':<7}"
        f"{'Weight':>9}"
        f"{'Momentum':>11}"
        f"{'Risk':>8}"
        f"{'Trend':>11}"
        f"{'Alignment':>12}"
    )

    print(header)
    print("-" * len(header))

    for item in analyzed_positions:

        print(
            f"{item.yahoo_symbol:<12}"
            f"{short_asset_name(item.position.name):<30}"
            f"{item.position.direction:<7}"
            f"{item.weight_pct:>8.2f}%"
            f"{item.scores.momentum_score:>10.1f}"
            f"{item.scores.risk_score:>8.1f}"
            f"{item.technical.trend:>11}"
            f"{item.scores.position_alignment:>12}"
        )


def print_price_gap_warnings(
    analyzed_positions: list[AnalyzedPosition],
) -> None:
    """
    Check differences between Fineco market prices and
    Yahoo Finance latest prices.

    This is a data-quality test, not a trading signal.

    Thresholds:

        abs(gap) < 1%  -> OK
        1% to 3%       -> WARNING
        > 3%           -> INVESTIGATE
    """

    print(
        "\n=== PRICE DATA QUALITY CHECK ===\n"
    )

    warnings_found = False

    for item in analyzed_positions:

        if not item.price_gap_comparable:
            print(
                f"{item.yahoo_symbol:<12} "
                f"PROXY - direct Fineco/provider price comparison not applicable"
            )
            continue

        gap = abs(
            item.price_gap_pct
        )

        if gap > 3:

            status = "INVESTIGATE"

        elif gap >= 1:

            status = "WARNING"

        else:

            continue

        warnings_found = True

        print(
            f"{item.yahoo_symbol:<12}"
            f"Fineco: "
            f"{item.position.market_price:>10.2f}  "
            f"Yahoo: "
            f"{item.technical.current_price:>10.2f}  "
            f"Gap: "
            f"{item.price_gap_pct:>+7.2f}%  "
            f"{status}"
        )

    if not warnings_found:

        print(
            "All Fineco/Yahoo price gaps "
            "are below 1%."
        )



def print_portfolio_risk_summary(
    risk: PortfolioRiskSummary,
) -> None:
    """Print long/short exposure and concentration metrics."""

    print("\n=== PORTFOLIO RISK SUMMARY ===\n")

    print(f"Positions:             {risk.positions:>8}")
    print(f"Long positions:        {risk.long_positions:>8}")
    print(f"Short positions:       {risk.short_positions:>8}")

    if risk.flat_positions:
        print(f"Flat positions:        {risk.flat_positions:>8}")

    print()
    print(f"Long exposure:       €{risk.long_exposure_eur:>14,.2f}")
    print(f"Short exposure:      €{risk.short_exposure_eur:>14,.2f}")
    print(f"Gross exposure:      €{risk.gross_exposure_eur:>14,.2f}")
    print(f"Net exposure:        €{risk.net_exposure_eur:>14,.2f}")
    print(f"Net / Gross:          {risk.net_to_gross_pct:>14.2f}%")

    print("\n--- Concentration (gross weights) ---\n")
    print(f"Largest position:      {risk.largest_position_pct:>12.2f}%")
    print(f"Top 3 concentration:   {risk.top3_concentration_pct:>12.2f}%")
    print(f"Top 5 concentration:   {risk.top5_concentration_pct:>12.2f}%")
    print(f"Top 10 concentration:  {risk.top10_concentration_pct:>12.2f}%")
    print(f"HHI:                    {risk.hhi:>12.4f}")
    print(f"Effective positions:    {risk.effective_positions:>12.2f}")

    print("\n--- Exposure-weighted technical risk ---\n")
    print(
        f"Weighted Vol20:        "
        f"{risk.weighted_volatility_20d_pct:>12.2f}%"
    )
    print(
        f"Weighted Tech Risk:    "
        f"{risk.weighted_technical_risk_score:>12.2f}/100"
    )


def print_position_risk_metrics(
    risk: PortfolioRiskSummary,
    analyzed_positions: list[AnalyzedPosition],
    limit: int | None = 15,
) -> None:
    """
    Print positions ranked by simple volatility load.

    VolLoad and TechLoad are Stage 2.1 exposure-weighted measures.
    They are NOT covariance-aware marginal risk contributions.
    """

    print("\n=== POSITION RISK LOAD ===\n")

    asset_names = {
        item.yahoo_symbol: item.position.name
        for item in analyzed_positions
    }

    header = (
        f"{'Symbol':<12}"
        f"{'Asset':<30}"
        f"{'Dir':<7}"
        f"{'Exposure':>13}"
        f"{'GrossW':>9}"
        f"{'NetW':>9}"
        f"{'Vol20':>9}"
        f"{'TechRisk':>10}"
        f"{'VolLoad':>10}"
        f"{'TechLoad':>10}"
    )

    print(header)
    print("-" * len(header))

    rows = sorted(
        risk.position_metrics,
        key=lambda metric: metric.volatility_load,
        reverse=True,
    )

    if limit is not None:
        rows = rows[:limit]

    for metric in rows:
        asset_name = asset_names.get(metric.yahoo_symbol, "")

        print(
            f"{metric.yahoo_symbol:<12}"
            f"{short_asset_name(asset_name):<30}"
            f"{metric.direction:<7}"
            f"{metric.exposure_eur:>13,.0f}"
            f"{metric.gross_weight_pct:>8.2f}%"
            f"{metric.net_weight_pct:>+8.2f}%"
            f"{metric.volatility_20d_pct:>8.1f}%"
            f"{metric.technical_risk_score:>10.1f}"
            f"{metric.volatility_load:>10.2f}"
            f"{metric.technical_risk_load:>10.2f}"
        )


def print_covariance_risk_summary(
    risk: PortfolioCovarianceRiskSummary,
    analyzed_positions: list[AnalyzedPosition],
    limit: int | None = 15,
) -> None:
    """Print Stage 2.2 covariance-aware portfolio market risk."""

    print("\n=== COVARIANCE-AWARE PORTFOLIO RISK ===\n")
    print(
        f"Extended risk window: max {RISK_MAX_SESSIONS} sessions (~3 years)"
    )
    print(
        f"Minimum common observations: {RISK_MIN_COMMON_OBSERVATIONS}\n"
    )

    print(f"Assets:                         {risk.assets:>8}")
    print(f"Common daily observations:      {risk.observations:>8}")
    print(f"Annualization factor:           {risk.annualization_factor:>8}")
    print(
        f"Portfolio volatility:          "
        f"{risk.portfolio_volatility_pct:>8.2f}%"
    )
    print(
        f"Weighted standalone volatility:"
        f"{risk.weighted_standalone_volatility_pct:>8.2f}%"
    )
    print(
        f"Diversification ratio:         "
        f"{risk.diversification_ratio:>8.2f}"
    )
    print(
        f"Risk coverage (gross exposure):"
        f"{risk.risk_coverage_pct:>8.2f}%"
    )

    if risk.excluded_symbols:
        print(
            "Excluded (short history):      "
            + ", ".join(risk.excluded_symbols)
        )

    print("\n=== COMPONENT RISK CONTRIBUTION ===\n")

    asset_names = {
        item.yahoo_symbol: item.position.name
        for item in analyzed_positions
    }

    header = (
        f"{'Symbol':<12}"
        f"{'Asset':<30}"
        f"{'Dir':<7}"
        f"{'SignedW':>10}"
        f"{'AssetVol':>10}"
        f"{'CompRisk':>11}"
        f"{'RiskContr':>11}"
    )
    print(header)
    print("-" * len(header))

    rows = sorted(
        risk.asset_metrics,
        key=lambda metric: abs(metric.risk_contribution_pct),
        reverse=True,
    )

    if limit is not None:
        rows = rows[:limit]

    for metric in rows:
        asset_name = asset_names.get(metric.yahoo_symbol, "")

        print(
            f"{metric.yahoo_symbol:<12}"
            f"{short_asset_name(asset_name):<30}"
            f"{metric.direction:<7}"
            f"{metric.signed_weight_pct:>+9.2f}%"
            f"{metric.annualized_volatility_pct:>9.2f}%"
            f"{metric.component_risk_pct_points:>+10.2f}%"
            f"{metric.risk_contribution_pct:>+10.2f}%"
        )

    total_component = sum(
        metric.component_risk_pct_points
        for metric in risk.asset_metrics
    )
    total_contribution = sum(
        metric.risk_contribution_pct
        for metric in risk.asset_metrics
    )

    print("-" * len(header))
    print(
        f"{'TOTAL':<29}"
        f"{'':>10}"
        f"{total_component:>+10.2f}%"
        f"{total_contribution:>+10.2f}%"
    )


def print_correlation_highlights(
    risk: PortfolioCovarianceRiskSummary,
    limit: int = 10,
) -> None:
    """Print the strongest absolute pairwise correlations."""

    corr = risk.correlation_matrix

    if getattr(corr, "empty", True) or len(corr.columns) < 2:
        return

    pairs = []
    symbols = list(corr.columns)

    for i, left in enumerate(symbols):
        for right in symbols[i + 1:]:
            value = float(corr.loc[left, right])
            pairs.append((abs(value), value, left, right))

    pairs.sort(reverse=True)

    print("\n=== CORRELATION HIGHLIGHTS ===\n")
    print(f"{'Asset 1':<12}{'Asset 2':<12}{'Corr':>9}")
    print("-" * 33)

    for _, value, left, right in pairs[:limit]:
        print(f"{left:<12}{right:<12}{value:>9.3f}")



def print_benchmark_risk_summary(
    risk: PortfolioBenchmarkRiskSummary,
    analyzed_positions: list[AnalyzedPosition],
    limit: int | None = 15,
) -> None:
    """Print Stage 2.4 benchmark-relative portfolio risk."""

    print(
        f"\n=== BENCHMARK RISK ({risk.benchmark_symbol}) ===\n"
    )

    print(f"Assets:                         {risk.assets:>8}")
    print(f"Common daily observations:      {risk.observations:>8}")
    print(f"Risk coverage (gross exposure): {risk.risk_coverage_pct:>7.2f}%")

    if risk.excluded_symbols:
        print(
            "Excluded (short history):      "
            + ", ".join(risk.excluded_symbols)
        )

    print()
    print(f"Portfolio beta:                 {risk.portfolio_beta:>8.3f}")
    print(f"Beta from components:           {risk.beta_from_components:>8.3f}")
    print(f"Annualized alpha:               {risk.alpha_annualized_pct:>+7.2f}%")
    print(f"Benchmark correlation:          {risk.correlation:>8.3f}")
    print(f"R-squared:                      {risk.r_squared:>8.3f}")
    print(f"Benchmark volatility:           {risk.benchmark_volatility_pct:>7.2f}%")
    print(f"Portfolio volatility:           {risk.portfolio_volatility_pct:>7.2f}%")
    print(f"Systematic volatility:          {risk.systematic_volatility_pct:>7.2f}%")
    print(f"Idiosyncratic volatility:       {risk.idiosyncratic_volatility_pct:>7.2f}%")

    print("\n=== BETA CONTRIBUTION ===\n")

    asset_names = {
        item.yahoo_symbol: item.position.name
        for item in analyzed_positions
    }

    header = (
        f"{'Symbol':<12}"
        f"{'Asset':<30}"
        f"{'Dir':<7}"
        f"{'SignedW':>10}"
        f"{'Beta':>9}"
        f"{'BetaCtr':>10}"
        f"{'AlphaAnn':>11}"
        f"{'Corr':>9}"
        f"{'R2':>8}"
    )

    print(header)
    print("-" * len(header))

    rows = sorted(
        risk.asset_metrics,
        key=lambda metric: abs(metric.beta_contribution),
        reverse=True,
    )

    if limit is not None:
        rows = rows[:limit]

    for metric in rows:
        asset_name = asset_names.get(metric.yahoo_symbol, "")

        print(
            f"{metric.yahoo_symbol:<12}"
            f"{short_asset_name(asset_name):<30}"
            f"{metric.direction:<7}"
            f"{metric.signed_weight_pct:>+9.2f}%"
            f"{metric.beta:>9.3f}"
            f"{metric.beta_contribution:>+10.3f}"
            f"{metric.alpha_annualized_pct:>+10.2f}%"
            f"{metric.correlation:>9.3f}"
            f"{metric.r_squared:>8.3f}"
        )

    if risk.risk_coverage_pct < 95:
        print(
            f"\nWARNING: Benchmark risk coverage is only "
            f"{risk.risk_coverage_pct:.2f}% of gross exposure."
        )


def print_multifactor_risk_summary(
    risk: PortfolioMultiFactorSummary,
    analyzed_positions: list[AnalyzedPosition],
    limit: int | None = 15,
) -> None:
    """Print Stage 2.5 multi-factor OLS risk analysis."""

    print("\n=== MULTI-FACTOR RISK ===\n")

    factors_label = ", ".join(
        f"{name}={symbol}"
        for name, symbol in zip(
            risk.factor_names,
            risk.factor_symbols,
        )
    )
    print(f"Factors:                        {factors_label}")
    print(f"Assets:                         {risk.assets:>8}")
    print(f"Common daily observations:      {risk.observations:>8}")
    print(f"Risk coverage (gross exposure): {risk.risk_coverage_pct:>7.2f}%")

    if risk.excluded_symbols:
        print(
            "Excluded (short history):      "
            + ", ".join(risk.excluded_symbols)
        )

    print()
    print(f"Annualized alpha:               {risk.alpha_annualized_pct:>+7.2f}%")
    print(f"R-squared:                      {risk.r_squared:>8.3f}")
    print(f"Adjusted R-squared:             {risk.adjusted_r_squared:>8.3f}")
    print(f"Portfolio volatility:           {risk.portfolio_volatility_pct:>7.2f}%")
    print(f"Systematic volatility:          {risk.systematic_volatility_pct:>7.2f}%")
    print(f"Idiosyncratic volatility:       {risk.idiosyncratic_volatility_pct:>7.2f}%")
    print(f"Factor condition number:        {risk.condition_number:>8.2f}")

    print("\n=== MULTI-FACTOR EXPOSURES ===\n")

    header = (
        f"{'Factor':<14}"
        f"{'Ticker':<10}"
        f"{'Beta':>10}"
        f"{'FromAssets':>12}"
        f"{'VIF':>10}"
    )
    print(header)
    print("-" * len(header))

    for metric in risk.factor_metrics:
        vif_text = (
            "INF"
            if metric.vif == float("inf")
            else f"{metric.vif:.2f}"
        )

        print(
            f"{metric.name:<14}"
            f"{metric.symbol:<10}"
            f"{metric.beta:>10.3f}"
            f"{metric.beta_from_assets:>12.3f}"
            f"{vif_text:>10}"
        )

    high_vif = [
        metric
        for metric in risk.factor_metrics
        if metric.vif > 5
    ]

    if risk.condition_number > 10 or high_vif:
        print("\nMulticollinearity warning:")

        if risk.condition_number > 10:
            print(
                f"  WARNING: factor condition number is "
                f"{risk.condition_number:.2f}."
            )

        for metric in high_vif:
            print(
                f"  WARNING: {metric.name} VIF is "
                f"{metric.vif:.2f}; individual factor beta may be unstable."
            )

    print("\n=== FACTOR CORRELATIONS ===\n")

    corr = risk.factor_correlation_matrix
    names = list(corr.columns)

    print(
        f"{'Factor 1':<14}"
        f"{'Factor 2':<14}"
        f"{'Corr':>9}"
    )
    print("-" * 37)

    factor_pairs = []

    for i, left in enumerate(names):
        for right in names[i + 1:]:
            value = float(corr.loc[left, right])
            factor_pairs.append(
                (abs(value), value, left, right)
            )

    factor_pairs.sort(reverse=True)

    for _, value, left, right in factor_pairs:
        print(
            f"{left:<14}"
            f"{right:<14}"
            f"{value:>9.3f}"
        )

    print("\n=== ASSET FACTOR BETA CONTRIBUTION ===\n")

    asset_names = {
        item.yahoo_symbol: item.position.name
        for item in analyzed_positions
    }

    dynamic_headers = "".join(
        f"{name[:8]:>10}"
        for name in risk.factor_names
    )

    header = (
        f"{'Symbol':<12}"
        f"{'Asset':<30}"
        f"{'Dir':<7}"
        f"{'SignedW':>10}"
        f"{dynamic_headers}"
    )
    print(header)
    print("-" * len(header))

    rows = sorted(
        risk.asset_metrics,
        key=lambda metric: sum(
            abs(value)
            for value in metric.factor_beta_contributions
        ),
        reverse=True,
    )

    if limit is not None:
        rows = rows[:limit]

    for metric in rows:
        asset_name = asset_names.get(
            metric.yahoo_symbol,
            "",
        )

        contributions = "".join(
            f"{value:>+10.3f}"
            for value in metric.factor_beta_contributions
        )

        print(
            f"{metric.yahoo_symbol:<12}"
            f"{short_asset_name(asset_name):<30}"
            f"{metric.direction:<7}"
            f"{metric.signed_weight_pct:>+9.2f}%"
            f"{contributions}"
        )

    if risk.risk_coverage_pct < 95:
        print(
            f"\nWARNING: Multi-factor risk coverage is only "
            f"{risk.risk_coverage_pct:.2f}% of gross exposure."
        )

def print_historical_tail_risk_summary(
    risk: PortfolioHistoricalTailRiskSummary,
) -> None:
    """Print Stage 2.3 Historical VaR and CVaR / Expected Shortfall."""

    print(
        f"\n=== HISTORICAL TAIL RISK ({risk.horizon_days}D) ===\n"
    )
    print(f"Assets:                         {risk.assets:>8}")
    print(f"Historical observations:       {risk.observations:>8}")
    print(f"Gross exposure:              €{risk.gross_exposure_eur:>14,.2f}")
    print(f"Risk coverage:                 {risk.risk_coverage_pct:>7.2f}%")
    print(f"Mean period return:            {risk.mean_return_pct:>+7.2f}%")
    print(f"Best period return:            {risk.best_return_pct:>+7.2f}%")
    print(f"Worst period return:           {risk.worst_return_pct:>+7.2f}%")
    print(f"Positive periods:              {risk.positive_periods_pct:>7.2f}%")

    if risk.excluded_symbols:
        print(
            "Excluded (short history):      "
            + ", ".join(risk.excluded_symbols)
        )

    print()
    header = (
        f"{'Confidence':>11}"
        f"{'VaR %':>11}"
        f"{'CVaR %':>11}"
        f"{'VaR EUR':>16}"
        f"{'CVaR EUR':>16}"
        f"{'Tail N':>9}"
    )
    print(header)
    print("-" * len(header))

    warnings: list[str] = []

    for level in risk.levels:
        print(
            f"{level.confidence_level_pct:>10.0f}%"
            f"{level.var_pct:>10.2f}%"
            f"{level.cvar_pct:>10.2f}%"
            f"  €{level.var_eur:>13,.2f}"
            f"  €{level.cvar_eur:>13,.2f}"
            f"{level.tail_observations:>9}"
        )

        if level.tail_observations < 5:
            warnings.append(
                f"{level.confidence_level_pct:.0f}% estimate is VERY LOW robustness: "
                f"only {level.tail_observations} tail observations."
            )
        elif level.tail_observations < 10:
            warnings.append(
                f"{level.confidence_level_pct:.0f}% estimate is LOW robustness: "
                f"only {level.tail_observations} tail observations."
            )
        elif level.tail_observations < 20:
            warnings.append(
                f"{level.confidence_level_pct:.0f}% estimate is MODERATE robustness: "
                f"{level.tail_observations} tail observations."
            )

    if warnings:
        print("\nStatistical robustness warnings:")
        for warning in warnings:
            print(f"  WARNING: {warning}")

    if risk.risk_coverage_pct < 95:
        print(
            f"  WARNING: Extended risk coverage is only "
            f"{risk.risk_coverage_pct:.2f}% of gross exposure."
        )

def print_summary(
    analyzed_positions: list[AnalyzedPosition],
) -> None:
    """
    Print basic portfolio-analysis integrity checks.
    """

    print(
        "\n=== SUMMARY ==="
    )

    print(
        f"Analyzed positions: "
        f"{len(analyzed_positions)}"
    )

    gross_exposure = sum(
        abs(item.position.market_value_eur)
        for item in analyzed_positions
        if item.position.direction != "FLAT"
    )

    total_weight = sum(
        item.weight_pct
        for item in analyzed_positions
    )

    print(
        f"Gross exposure:     "
        f"€{gross_exposure:,.2f}"
    )

    print(
        f"Total weight:       "
        f"{total_weight:.2f}%"
    )



# =============================================================
# Stage 3 Quant Engine -> CIO persistence bridge
# =============================================================


def _sha256_file(
    file_path: str | Path,
) -> str:
    """
    Hash the input workbook as it exists before the current report
    export modifies/rewrites Portfolio Analysis sheets.
    """

    path = Path(
        file_path
    )

    digest = hashlib.sha256()

    with path.open(
        "rb"
    ) as handle:

        for chunk in iter(
            lambda: handle.read(
                1024 * 1024
            ),
            b"",
        ):
            digest.update(
                chunk
            )

    return digest.hexdigest()


def _new_snapshot_id(
    now: datetime,
) -> str:

    return (
        f"SNAP-"
        f"{now:%Y%m%d-%H%M%S}-"
        f"{uuid4().hex[:6]}"
    )


def _new_risk_state_id(
    now: datetime,
) -> str:

    return (
        f"RISK-"
        f"{now:%Y%m%d-%H%M%S}-"
        f"{uuid4().hex[:6]}"
    )


def _find_tail_level(
    historical_risk: PortfolioHistoricalTailRiskSummary,
    confidence_level_pct: float,
):
    """
    Return one Historical VaR/CVaR level by confidence percentage.
    """

    for level in historical_risk.levels:

        if (
            abs(
                level.confidence_level_pct
                - confidence_level_pct
            )
            < 1e-9
        ):
            return level

    raise ValueError(
        "Historical tail-risk level not found: "
        f"{confidence_level_pct:.0f}%"
    )


def persist_stage3_analysis_state(
    *,
    portfolio_file: str | Path,
    analyzed_positions: list[AnalyzedPosition],
    portfolio_risk: PortfolioRiskSummary,
    covariance_risk: PortfolioCovarianceRiskSummary,
    benchmark_risk: PortfolioBenchmarkRiskSummary,
    historical_risk_1d: PortfolioHistoricalTailRiskSummary,
    db_path: str | Path = STAGE3_DB_PATH,
) -> tuple[
    PortfolioSnapshot,
    PortfolioRiskStateRecord,
]:
    """
    Persist the canonical Stage 3 snapshot and quantitative BEFORE state
    produced by the completed Portfolio Analysis run.

    No analytical metric is recalculated here: this function only maps
    already-computed Quant Engine results into Stage 3 canonical models.
    """

    now = datetime.now(
        timezone.utc
    )

    store = Stage3Store(
        db_path
    )

    account_state = (
        store.get_latest_account_state()
    )

    account_state_id = (
        account_state.account_state_id
        if account_state is not None
        else None
    )

    snapshot = PortfolioSnapshot(
        snapshot_id=(
            _new_snapshot_id(
                now
            )
        ),
        timestamp=now,
        source_file=str(
            Path(
                portfolio_file
            )
        ),
        source_file_hash=(
            _sha256_file(
                portfolio_file
            )
        ),
        quant_engine_version=(
            QUANT_ENGINE_VERSION
        ),
        analyzed_positions=(
            len(
                analyzed_positions
            )
        ),
        gross_exposure_eur=(
            portfolio_risk.gross_exposure_eur
        ),
        net_exposure_eur=(
            portfolio_risk.net_exposure_eur
        ),
        account_state_id=(
            account_state_id
        ),
    )

    tail_95 = (
        _find_tail_level(
            historical_risk_1d,
            95.0,
        )
    )

    # Conservative coverage for the metrics represented in
    # PortfolioRiskState: use the weakest coverage among the engines
    # supplying volatility, beta and VaR/CVaR.
    analytical_coverage_pct = min(
        covariance_risk.risk_coverage_pct,
        benchmark_risk.risk_coverage_pct,
        historical_risk_1d.risk_coverage_pct,
    )

    risk_state = PortfolioRiskState(
        gross_exposure_eur=(
            portfolio_risk.gross_exposure_eur
        ),
        net_exposure_eur=(
            portfolio_risk.net_exposure_eur
        ),
        long_exposure_eur=(
            portfolio_risk.long_exposure_eur
        ),
        short_exposure_eur=(
            portfolio_risk.short_exposure_eur
        ),
        portfolio_volatility_pct=(
            covariance_risk.portfolio_volatility_pct
        ),
        portfolio_beta=(
            benchmark_risk.portfolio_beta
        ),
        var_95_1d_eur=(
            tail_95.var_eur
        ),
        cvar_95_1d_eur=(
            tail_95.cvar_eur
        ),
        top5_concentration_pct=(
            portfolio_risk.top5_concentration_pct
        ),
        effective_positions=(
            portfolio_risk.effective_positions
        ),
        analytical_coverage_pct=(
            analytical_coverage_pct
        ),
    )

    record = PortfolioRiskStateRecord(
        risk_state_id=(
            _new_risk_state_id(
                now
            )
        ),
        snapshot_id=(
            snapshot.snapshot_id
        ),
        created_at=now,
        state=risk_state,
    )

    # Snapshot first because save_portfolio_risk_state() enforces
    # referential consistency.
    store.save_portfolio_snapshot(
        snapshot
    )

    store.save_portfolio_risk_state(
        record
    )

    return (
        snapshot,
        record,
    )


# =============================================================
# Standalone execution
# =============================================================

if __name__ == "__main__":

    portfolio_file = Path(
        "data/input/portafoglio-export.xlsx"
    )

    # ---------------------------------------------------------
    # Run complete portfolio analysis
    # ---------------------------------------------------------

    analyzed_positions = (
        analyze_portfolio(
            portfolio_file
        )
    )

    # ---------------------------------------------------------
    # Technical table
    # ---------------------------------------------------------

    print_portfolio_analysis(
        analyzed_positions
    )

    # ---------------------------------------------------------
    # Scoring table
    # ---------------------------------------------------------

    print_scoring_analysis(
        analyzed_positions
    )

    # ---------------------------------------------------------
    # Portfolio risk foundation (LONG/SHORT aware)
    # ---------------------------------------------------------

    risk_summary = calculate_portfolio_risk(
        analyzed_positions
    )

    print_portfolio_risk_summary(
        risk_summary
    )

    print_position_risk_metrics(
        risk_summary,
        analyzed_positions,
    )

    # ---------------------------------------------------------
    # Stage 2.2 covariance-aware risk
    # ---------------------------------------------------------

    covariance_risk = calculate_covariance_risk(
        analyzed_positions,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )

    print_covariance_risk_summary(
        covariance_risk,
        analyzed_positions,
    )

    print_correlation_highlights(
        covariance_risk
    )

    # ---------------------------------------------------------
    # Stage 2.4 Benchmark-relative risk
    # ---------------------------------------------------------

    benchmark_history = get_price_history(
        BENCHMARK_SYMBOL,
        period=MARKET_DATA_DOWNLOAD_PERIOD,
    )

    benchmark_risk = calculate_benchmark_risk(
        analyzed_positions,
        benchmark_symbol=BENCHMARK_SYMBOL,
        benchmark_history=benchmark_history,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )

    print_benchmark_risk_summary(
        benchmark_risk,
        analyzed_positions,
    )

    # ---------------------------------------------------------
    # Stage 2.5 Multi-factor risk
    # ---------------------------------------------------------

    factor_histories = download_price_history(
        MULTI_FACTOR_SYMBOLS.values(),
        period=MARKET_DATA_DOWNLOAD_PERIOD,
    )

    multifactor_risk = calculate_multifactor_risk(
        analyzed_positions,
        factor_definitions=MULTI_FACTOR_SYMBOLS,
        factor_histories=factor_histories,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )

    print_multifactor_risk_summary(
        multifactor_risk,
        analyzed_positions,
    )

    # ---------------------------------------------------------
    # Stage 2.3 Historical VaR / CVaR (Expected Shortfall)
    # ---------------------------------------------------------

    historical_risk_1d = calculate_historical_tail_risk(
        analyzed_positions,
        confidence_levels=(0.95, 0.99),
        horizon_days=1,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )

    print_historical_tail_risk_summary(
        historical_risk_1d
    )

    historical_risk_10d = calculate_historical_tail_risk(
        analyzed_positions,
        confidence_levels=(0.95, 0.99),
        horizon_days=10,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )

    print_historical_tail_risk_summary(
        historical_risk_10d
    )

    # ---------------------------------------------------------
    # Data-quality checks
    # ---------------------------------------------------------

    print_price_gap_warnings(
        analyzed_positions
    )

    # ---------------------------------------------------------
    # Integrity summary
    # ---------------------------------------------------------

    print_summary(
        analyzed_positions
    )

    # ---------------------------------------------------------
    # Stage 3 persistence bridge
    #
    # Persist the exact analytical state used by the CIO before
    # writing/updating the Excel reporting sheets.
    # ---------------------------------------------------------

    try:

        (
            stage3_snapshot,
            stage3_risk_state,
        ) = persist_stage3_analysis_state(
            portfolio_file=portfolio_file,
            analyzed_positions=analyzed_positions,
            portfolio_risk=risk_summary,
            covariance_risk=covariance_risk,
            benchmark_risk=benchmark_risk,
            historical_risk_1d=historical_risk_1d,
        )

        print(
            "\n=== STAGE 3 CIO STATE PERSISTED ===\n"
        )

        print(
            f"PortfolioSnapshot: "
            f"{stage3_snapshot.snapshot_id}"
        )

        print(
            f"PortfolioRiskState: "
            f"{stage3_risk_state.risk_state_id}"
        )

        print(
            f"Analytical coverage: "
            f"{stage3_risk_state.state.analytical_coverage_pct:.2f}%"
        )

    except Exception as exc:

        print(
            "\nWARNING: Stage 3 CIO state could not be persisted."
        )

        print(
            f"Details: {exc}"
        )

    # ---------------------------------------------------------
    # Excel report - append Portfolio Analysis sheets to the
    # original Fineco workbook without altering original sheets.
    # ---------------------------------------------------------

    try:
        report_file = export_portfolio_analysis_to_excel(
            portfolio_file,
            analyzed_positions=analyzed_positions,
            portfolio_risk=risk_summary,
            covariance_risk=covariance_risk,
            benchmark_risk=benchmark_risk,
            multifactor_risk=multifactor_risk,
            tail_risk_1d=historical_risk_1d,
            tail_risk_10d=historical_risk_10d,
        )

        print(
            f"\nExcel analysis written to: {report_file}"
        )

    except PermissionError as exc:
        print(
            "\nWARNING: Excel report could not be written because "
            "the workbook is probably open in Excel. Close the file "
            "and run the analysis again."
        )
        print(f"Details: {exc}")
