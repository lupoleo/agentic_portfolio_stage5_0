from app.portfolio.models import PortfolioPosition


SPECIAL_TICKER_MAP = {
    # ---------------------------------------------------------
    # Fineco CFD / CFDC symbols requiring explicit mapping
    # to the Yahoo Finance underlying ticker.
    #
    # Fineco derivative symbols are not always obtained simply
    # by appending/removing "CFD.CFD".  Therefore instruments
    # with non-standard broker codes must be mapped explicitly.
    # ---------------------------------------------------------
    # Bolsa de Madrid
    "CIRSA.MC": "CIRSA.MC",
    "MELICFD.CFD": "MELI",
    "ANETICFD.CFD": "ANET",
    "TSLAXCFD.CFD": "TSLA",
    "CERTIT0005687634.HICERT": "LTMC.MI",

    # Super Micro Computer
    #
    # Fineco ordinary stock:
    #     SMCI.O
    # resolves automatically to:
    #     SMCI
    #
    # Fineco CFDC:
    #     SMCIICFD.CFD
    # must explicitly resolve to the same Yahoo underlying:
    #     SMCI
    #
    # Without this mapping the generic CFD fallback would produce
    # the invalid Yahoo ticker "SMCII".
    "SMCIICFD.CFD": "SMCI",

    # ---------------------------------------------------------
    # XETRA / Frankfurt cases explicitly mapped to the
    # corresponding Yahoo Finance German listing.
    # ---------------------------------------------------------

    "2BTC.FRA": "2BTC.DE",
    "VDIV.FRA": "VDIV.DE",
    "C001.FRA": "C001.DE",
    "NUKL.FRA": "NUKL.DE",
    "EUNL.FRA": "EUNL.DE",
}


def resolve_yahoo_symbol(
    position: PortfolioPosition,
) -> str | None:
    """
    Resolve a Fineco broker symbol into the Yahoo Finance symbol
    used by the Portfolio Analysis market-data layer.

    Resolution priority:

        1. Explicit special mappings.
        2. Fineco NASDAQ ordinary shares (.O).
        3. Fineco NYSE ordinary shares (.N).
        4. Borsa Italiana / Milano instruments (.MI).
        5. Generic Fineco CFD fallback.
        6. Otherwise unresolved.

    Explicit mappings always take precedence because some Fineco
    derivative symbols do not have a one-to-one syntactic mapping
    to the underlying Yahoo ticker.
    """

    symbol = position.broker_symbol

    # A reviewed reference always wins over syntactic fallbacks. This is
    # required for derivatives such as KERCFD.CFD, whose broker code would
    # otherwise be incorrectly reduced to KER instead of KER.PA.
    if position.market_data_symbol is not None:
        return position.market_data_symbol

    # ---------------------------------------------------------
    # Explicit mappings first
    # ---------------------------------------------------------

    if symbol in SPECIAL_TICKER_MAP:
        return SPECIAL_TICKER_MAP[
            symbol
        ]

    # ---------------------------------------------------------
    # Fineco NASDAQ symbols
    #
    # Example:
    #     SMCI.O -> SMCI
    #     MSFT.O -> MSFT
    #     NVDA.O -> NVDA
    # ---------------------------------------------------------

    if symbol.endswith(".O"):
        return symbol.removesuffix(
            ".O"
        )

    # ---------------------------------------------------------
    # Fineco NYSE symbols
    #
    # Example:
    #     RTX.N -> RTX
    # ---------------------------------------------------------

    if symbol.endswith(".N"):
        return symbol.removesuffix(
            ".N"
        )

    # ---------------------------------------------------------
    # Borsa Italiana / ETF Milano
    #
    # Yahoo uses the same .MI suffix.
    # ---------------------------------------------------------

    if symbol.endswith(".MI"):
        return symbol

    # ---------------------------------------------------------
    # Fineco CFDs / CFDCs generic fallback
    #
    # This works only when the broker symbol is structurally:
    #
    #     <YAHOO_TICKER>CFD.CFD
    #
    # Example:
    #
    #     MUCFD.CFD -> MU
    #
    # Non-standard cases such as:
    #
    #     SMCIICFD.CFD
    #
    # MUST be handled in SPECIAL_TICKER_MAP above.
    # ---------------------------------------------------------

    if symbol.endswith(
        "CFD.CFD"
    ):
        return symbol.removesuffix(
            "CFD.CFD"
        )

    return None


# =============================================================
# Standalone validation
# =============================================================

if __name__ == "__main__":

    from pathlib import Path

    from app.market_data.yahoo_provider import (
        symbol_exists,
    )

    from app.portfolio.fineco_importer import (
        load_fineco_positions,
    )

    portfolio_file = Path(
        "data/input/portafoglio-export.xlsx"
    )

    positions = (
        load_fineco_positions(
            portfolio_file
        )
    )

    print(
        "\n=== YAHOO TICKER VALIDATION ===\n"
    )

    resolved = 0
    failed = 0

    for position in positions:

        yahoo_symbol = (
            resolve_yahoo_symbol(
                position
            )
        )

        if yahoo_symbol is None:

            status = "UNRESOLVED"
            failed += 1

        elif symbol_exists(
            yahoo_symbol
        ):

            status = "OK"
            resolved += 1

        else:

            status = "NOT FOUND"
            failed += 1

        print(
            f"{position.name[:42]:42} "
            f"{position.broker_symbol:12} "
            f"-> {str(yahoo_symbol):12} "
            f"{status}"
        )

    print(
        "\n=== SUMMARY ==="
    )

    print(
        f"Positions: {len(positions)}"
    )

    print(
        f"Resolved:  {resolved}"
    )

    print(
        f"Failed:    {failed}"
    )
