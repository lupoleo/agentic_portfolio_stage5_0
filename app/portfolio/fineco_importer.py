from pathlib import Path

import pandas as pd

from app.portfolio.models import PortfolioPosition
from app.portfolio.instrument_reference import enrich_position


def load_fineco_dataframe(file_path: str | Path) -> pd.DataFrame:
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Fineco file not found: {file_path}")

    df = pd.read_excel(
        file_path,
        sheet_name="Portafoglio sintesi",
        header=2,
        engine="openpyxl",
    )

    return df


def extract_positions_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # Find the row where Fineco starts the totals section
    total_rows = df.index[df["Titolo"] == "Totale"]

    if len(total_rows) == 0:
        raise ValueError("Fineco totals row not found")

    total_index = total_rows[0]

    # Everything before "Totale" belongs to the positions area
    positions_df = df.loc[: total_index - 1].copy()

    # Remove completely empty rows
    positions_df = positions_df.dropna(how="all")

    # Remove rows without a title
    positions_df = positions_df[
        positions_df["Titolo"].notna()
    ]

    return positions_df


def safe_float(value) -> float:
    if pd.isna(value):
        return 0.0

    return float(value)


def row_to_position(row: pd.Series) -> PortfolioPosition:
    position = PortfolioPosition(
        name=str(row["Titolo"]),
        isin=str(row["ISIN"]),
        broker_symbol=str(row["Simbolo"]),
        market=str(row["Mercato"]),
        instrument_type=str(row["Strumento"]),
        currency=str(row["Valuta"]),

        quantity=safe_float(row["Quantità"]),
        average_price=safe_float(row["P.zo medio di carico"]),
        load_exchange_rate=safe_float(row["Cambio di carico"]),
        cost_value_eur=safe_float(row["Valore di carico"]),

        market_price=safe_float(row["P.zo di mercato"]),
        market_exchange_rate=safe_float(row["Cambio di mercato"]),
        market_value_eur=safe_float(row["Valore di mercato €"]),

        pnl_percent=safe_float(row["Var%"]),
        pnl_eur=safe_float(row["Var €"]),
        pnl_currency=safe_float(row["Var in valuta"]),

        accrued_interest=safe_float(row["Rateo"]),
    )

    return enrich_position(position)


def load_fineco_positions(
    file_path: str | Path,
) -> list[PortfolioPosition]:

    df = load_fineco_dataframe(file_path)

    positions_df = extract_positions_dataframe(df)

    positions = [
        row_to_position(row)
        for _, row in positions_df.iterrows()
    ]

    return positions


if __name__ == "__main__":
    portfolio_file = Path(
        "data/input/portafoglio-export.xlsx"
    )

    positions = load_fineco_positions(portfolio_file)

    print("\n=== FINECO PORTFOLIO ===")
    print(f"Positions: {len(positions)}")

    print("\n=== FIRST 5 POSITIONS ===")

    for position in positions[:5]:
        print(
            f"{position.name:20} "
            f"{position.broker_symbol:12} "
            f"{position.direction:5} "
            f"Qty: {position.quantity:10.2f} "
            f"Value: €{position.market_value_eur:,.2f} "
            f"P/L: {position.pnl_percent:+.2f}%"
        )
