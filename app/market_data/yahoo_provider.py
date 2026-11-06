from collections.abc import Iterable

import pandas as pd
import yfinance as yf


def get_price_history(
    symbol: str,
    period: str = "1y",
) -> pd.DataFrame:
    """
    Download historical market data for a single Yahoo Finance symbol.
    """

    ticker = yf.Ticker(symbol)

    history = ticker.history(
        period=period,
        auto_adjust=True,
    )

    return history


def symbol_exists(symbol: str) -> bool:
    """
    Return True if Yahoo Finance provides price history for the symbol.
    """

    try:
        history = get_price_history(symbol, period="5d")
        return not history.empty

    except Exception:
        return False


def download_price_history(
    symbols: Iterable[str],
    period: str = "1y",
) -> dict[str, pd.DataFrame]:
    """
    Download historical data for multiple Yahoo Finance symbols.

    Duplicate symbols are removed automatically.

    Returns:
        dict:
            {
                "NVDA": DataFrame,
                "MSFT": DataFrame,
                ...
            }
    """

    unique_symbols = sorted(set(symbols))

    if not unique_symbols:
        return {}

    print(
        f"Downloading market data for "
        f"{len(unique_symbols)} unique symbols..."
    )

    raw_data = yf.download(
        tickers=unique_symbols,
        period=period,
        group_by="ticker",
        auto_adjust=True ,
        threads=True,
        progress=False,
    )

    result: dict[str, pd.DataFrame] = {}

    for symbol in unique_symbols:

        try:
            if len(unique_symbols) == 1:
                history = raw_data.copy()
            else:
                history = raw_data[symbol].copy()

            # Remove dates where no price information exists
            history = history.dropna(
                subset=["Close"],
                how="all",
            )

            if not history.empty:
                result[symbol] = history

        except (KeyError, TypeError):
            # Symbol not returned correctly by Yahoo
            continue

    return result

if __name__ == "__main__":

    from pathlib import Path

    from app.portfolio.fineco_importer import load_fineco_positions
    from app.portfolio.ticker_resolver import resolve_yahoo_symbol

    portfolio_file = Path(
        "data/input/portafoglio-export.xlsx"
    )

    positions = load_fineco_positions(
        portfolio_file
    )

    symbols = []

    for position in positions:

        yahoo_symbol = resolve_yahoo_symbol(position)

        if yahoo_symbol is not None:
            symbols.append(yahoo_symbol)

    market_data = download_price_history(
        symbols,
        period="1y",
    )

    print("\n=== MARKET DATA DOWNLOAD ===")
    print(f"Portfolio positions: {len(positions)}")
    print(f"Yahoo symbols:       {len(symbols)}")
    print(f"Unique symbols:      {len(set(symbols))}")
    print(f"Downloaded:          {len(market_data)}")

    print("\n=== DATA AVAILABLE ===")

    for symbol, history in market_data.items():

        last_close = history["Close"].iloc[-1]

        print(
            f"{symbol:12} "
            f"Rows: {len(history):4} "
            f"Last close: {last_close:10.2f}"
        )

def get_raw_price_history(
    symbol: str,
    period: str = "1y",
) -> pd.DataFrame:
    """
    Download unadjusted historical prices.

    Used for nominal market-price checks and nominal
    52-week high/low calculations.
    """

    ticker = yf.Ticker(symbol)

    return ticker.history(
        period=period,
        auto_adjust=False,
    )