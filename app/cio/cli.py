from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from app.cio.models import (
    AccountState,
    CacheStatus,
    CertificateTerms,
    Currency,
    CurrencyCash,
    DataSource,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentType,
    RiskConstraints,
    TradingMode,
    Direction,
    OpportunityStatus,
    TradeOpportunity,
    TradeProposal,
    PortfolioSimulation,
    CioDecision,
    ExecutionPlan,
    OperatorConfirmation,
    OperatorConfirmationOutcome,
    TradeExitReason,
    TradeOutcome,
    TradeOutcomeStatus,
    TradingHorizon,
)

from app.cio.instrument_selector import (
    InstrumentSelector,
)

from app.cio.portfolio_filter_cli import (
    assess_portfolio_fit_cli,
    show_portfolio_fit_cli,
)
from app.cio.portfolio_lifecycle_gate import (
    PortfolioLifecycleAction,
    PortfolioLifecycleGate,
)
from app.cio.storage import Stage3Store


DEFAULT_DB_PATH = Path(
    "data/state/portfolio_cio.db"
)

from app.cio.bulk_importer import (
    FinecoBulkImporter,
    print_bulk_import_result,
)

from app.cio.position_sizer import (
    PositionSizer,
)

from app.cio.trade_proposal_service import (
    TradeProposalService,
)

from app.cio.portfolio_simulation_factory import (
    build_canonical_portfolio_simulation_service,
)
from app.cio.portfolio_simulation_service import (
    PortfolioSimulationService,
)

from app.cio.cio_decision_service import (
    CioDecisionService,
)

from app.cio.execution_plan_service import (
    ExecutionPlanService,
)


from app.cio.operator_confirmation_service import (
    OperatorConfirmationService,
)


from app.cio.trade_outcome_service import (
    TradeOutcomeService,
)


# =============================================================
# Generic input helpers
# =============================================================


def _display_default(
    value: object,
) -> str:

    if value is None:
        return "UNKNOWN"

    if isinstance(value, bool):
        return "YES" if value else "NO"

    if isinstance(value, Enum):
        return value.value

    return str(value)


def _prompt_text_default(
    label: str,
    current: str | None,
    *,
    required: bool = False,
) -> str | None:
    """
    ENTER keeps the current value.

    '-' clears an optional value.
    """

    while True:

        raw = input(
            f"{label} "
            f"[{_display_default(current)}]: "
        ).strip()

        if raw == "":
            return current

        if raw == "-":

            if required:
                print(
                    "This field cannot be cleared."
                )
                continue

            return None

        return raw


def _prompt_float_default(
    label: str,
    current: float | None,
    *,
    minimum: float | None = None,
) -> float | None:
    """
    ENTER keeps current value.

    '-' sets the value to UNKNOWN / None.
    """

    while True:

        raw = input(
            f"{label} "
            f"[{_display_default(current)}]: "
        ).strip()

        if raw == "":
            return current

        if raw == "-":
            return None

        try:
            value = float(
                raw.replace(",", ".")
            )

        except ValueError:
            print(
                "Please enter a valid number."
            )
            continue

        if (
            minimum is not None
            and value < minimum
        ):
            print(
                f"Value must be >= {minimum}."
            )
            continue

        return value


def _prompt_bool_default(
    label: str,
    current: bool | None,
) -> bool | None:
    """
    ENTER keeps current value.

    y = YES
    n = NO
    ? = UNKNOWN
    """

    while True:

        raw = input(
            f"{label} "
            f"[{_display_default(current)}] "
            "[y/n/?]: "
        ).strip().lower()

        if raw == "":
            return current

        if raw in {
            "y",
            "yes",
            "s",
            "si",
            "sì",
        }:
            return True

        if raw in {
            "n",
            "no",
        }:
            return False

        if raw in {
            "?",
            "unknown",
            "u",
        }:
            return None

        print(
            "Enter y, n or ?."
        )


def _prompt_choice_default(
    label: str,
    choices: list[str],
    current: str,
) -> str:

    normalized = {
        choice.upper(): choice.upper()
        for choice in choices
    }

    while True:

        raw = input(
            f"{label} "
            f"[{current}] "
            f"({'/'.join(choices)}): "
        ).strip().upper()

        if raw == "":
            return current

        if raw in normalized:
            return normalized[raw]

        print(
            "Invalid choice."
        )


def _prompt_optional_choice_default(
    label: str,
    choices: list[str],
    current: str | None,
) -> str | None:
    """
    ENTER keeps current value.

    '-' clears it.
    """

    normalized = {
        choice.upper(): choice.upper()
        for choice in choices
    }

    while True:

        raw = input(
            f"{label} "
            f"[{_display_default(current)}]: "
        ).strip().upper()

        if raw == "":
            return current

        if raw == "-":
            return None

        if raw in normalized:
            return normalized[raw]

        print(
            "Invalid choice. "
            "ENTER keeps current; "
            "'-' clears the value."
        )


def _prompt_float(
    label: str,
    *,
    optional: bool = False,
    minimum: float | None = None,
) -> float | None:

    while True:

        raw = input(label).strip()

        if raw == "" and optional:
            return None

        try:
            value = float(
                raw.replace(",", ".")
            )

        except ValueError:
            print(
                "Please enter a valid number."
            )
            continue

        if (
            minimum is not None
            and value < minimum
        ):
            print(
                f"Value must be >= {minimum}."
            )
            continue

        return value


def _prompt_bool_optional(
    label: str,
) -> bool | None:

    while True:

        raw = input(
            f"{label} [y/n/?]: "
        ).strip().lower()

        if raw in {
            "y",
            "yes",
            "s",
            "si",
            "sì",
        }:
            return True

        if raw in {
            "n",
            "no",
        }:
            return False

        if raw in {
            "",
            "?",
            "unknown",
            "u",
        }:
            return None

        print(
            "Enter y, n or ? for unknown."
        )


def _prompt_choice(
    label: str,
    choices: list[str],
) -> str:

    normalized = {
        choice.upper(): choice.upper()
        for choice in choices
    }

    while True:

        raw = input(
            f"{label} "
            f"({'/'.join(choices)}): "
        ).strip().upper()

        if raw in normalized:
            return normalized[raw]

        print(
            "Invalid choice."
        )


def _prompt_optional_choice(
    label: str,
    choices: list[str],
) -> str | None:

    normalized = {
        choice.upper(): choice.upper()
        for choice in choices
    }

    while True:

        raw = input(
            f"{label} "
            f"({'/'.join(choices)}) "
            "[optional]: "
        ).strip().upper()

        if raw == "":
            return None

        if raw in normalized:
            return normalized[raw]

        print(
            "Invalid choice. "
            "Press ENTER for UNKNOWN."
        )


def _prompt_date_optional(
    label: str,
) -> date | None:
    """
    Read YYYY-MM-DD.

    ENTER = UNKNOWN.
    """

    while True:

        raw = input(
            f"{label} "
            "[YYYY-MM-DD, optional]: "
        ).strip()

        if raw == "":
            return None

        try:
            return date.fromisoformat(
                raw
            )

        except ValueError:
            print(
                "Please enter a valid date "
                "in YYYY-MM-DD format."
            )


def _prompt_date_default(
    label: str,
    current: date | None,
) -> date | None:
    """
    ENTER keeps current value.

    '-' clears the value.
    """

    while True:

        raw = input(
            f"{label} "
            f"[{_display_default(current)}] "
            "[YYYY-MM-DD]: "
        ).strip()

        if raw == "":
            return current

        if raw == "-":
            return None

        try:
            return date.fromisoformat(
                raw
            )

        except ValueError:
            print(
                "Please enter a valid date "
                "in YYYY-MM-DD format."
            )


# =============================================================
# Certificate helpers
# =============================================================


def _read_certificate_terms_new(
) -> CertificateTerms:

    print(
        "\nCertificate terms: "
        "leave fields blank when unknown.\n"
    )

    issuer = (
        input(
            "Certificate issuer "
            "[optional]: "
        ).strip()
        or None
    )

    certificate_type = (
        input(
            "Certificate type/payoff "
            "[optional]: "
        ).strip()
        or None
    )

    maturity_date = (
        _prompt_date_optional(
            "Maturity date"
        )
    )

    strike = _prompt_float(
        "Strike [optional]: ",
        optional=True,
        minimum=0,
    )

    barrier = _prompt_float(
        "Barrier [optional]: ",
        optional=True,
        minimum=0,
    )

    barrier_pct = _prompt_float(
        "Barrier % [optional]: ",
        optional=True,
        minimum=0,
    )

    cap = _prompt_float(
        "Cap [optional]: ",
        optional=True,
        minimum=0,
    )

    ratio = _prompt_float(
        "Ratio [optional]: ",
        optional=True,
        minimum=0.00000001,
    )

    participation_pct = (
        _prompt_float(
            "Participation % "
            "[optional]: ",
            optional=True,
            minimum=0,
        )
    )

    coupon_pct = _prompt_float(
        "Coupon % [optional]: ",
        optional=True,
    )

    isin = (
        input(
            "ISIN [optional]: "
        ).strip()
        or None
    )

    payoff_notes = (
        input(
            "Payoff notes [optional]: "
        ).strip()
        or None
    )

    return CertificateTerms(
        issuer=issuer,
        certificate_type=certificate_type,
        maturity_date=maturity_date,
        strike=strike,
        barrier=barrier,
        barrier_pct=barrier_pct,
        cap=cap,
        ratio=ratio,
        participation_pct=(
            participation_pct
        ),
        coupon_pct=coupon_pct,
        isin=isin,
        payoff_notes=payoff_notes,
    )


def _read_certificate_terms_edit(
    current: CertificateTerms | None,
) -> CertificateTerms:

    current = (
        current
        or CertificateTerms()
    )

    print(
        "\nCertificate terms: "
        "ENTER keeps current value; "
        "'-' clears an optional value.\n"
    )

    issuer = _prompt_text_default(
        "Certificate issuer",
        current.issuer,
    )

    certificate_type = (
        _prompt_text_default(
            "Certificate type/payoff",
            current.certificate_type,
        )
    )

    maturity_date = (
        _prompt_date_default(
            "Maturity date",
            current.maturity_date,
        )
    )

    strike = _prompt_float_default(
        "Strike",
        current.strike,
        minimum=0,
    )

    barrier = _prompt_float_default(
        "Barrier",
        current.barrier,
        minimum=0,
    )

    barrier_pct = (
        _prompt_float_default(
            "Barrier %",
            current.barrier_pct,
            minimum=0,
        )
    )

    cap = _prompt_float_default(
        "Cap",
        current.cap,
        minimum=0,
    )

    ratio = _prompt_float_default(
        "Ratio",
        current.ratio,
        minimum=0.00000001,
    )

    participation_pct = (
        _prompt_float_default(
            "Participation %",
            current.participation_pct,
            minimum=0,
        )
    )

    coupon_pct = (
        _prompt_float_default(
            "Coupon %",
            current.coupon_pct,
        )
    )

    isin = _prompt_text_default(
        "ISIN",
        current.isin,
    )

    payoff_notes = (
        _prompt_text_default(
            "Payoff notes",
            current.payoff_notes,
        )
    )

    return CertificateTerms(
        issuer=issuer,
        certificate_type=certificate_type,
        maturity_date=maturity_date,
        strike=strike,
        barrier=barrier,
        barrier_pct=barrier_pct,
        cap=cap,
        ratio=ratio,
        participation_pct=(
            participation_pct
        ),
        coupon_pct=coupon_pct,
        isin=isin,
        payoff_notes=payoff_notes,
    )


# =============================================================
# IDs
# =============================================================


def _new_account_state_id(
    now: datetime,
) -> str:

    return (
        f"ACC-"
        f"{now:%Y%m%d-%H%M%S}-"
        f"{uuid4().hex[:6]}"
    )


def _new_instrument_id(
    underlying: str,
    instrument_type: InstrumentType,
    trading_mode: TradingMode,
    broker_leverage: float | None,
) -> str:

    leverage_part = (
        f"X{broker_leverage:g}"
        if broker_leverage is not None
        else "XNA"
    )

    return (
        f"FIN-"
        f"{underlying.upper()}-"
        f"{instrument_type.value}-"
        f"{trading_mode.value}-"
        f"{leverage_part}-"
        f"{uuid4().hex[:6]}"
    )

def _new_opportunity_id(
    ticker: str,
    now: datetime,
) -> str:

    return (
        f"OPP-"
        f"{ticker.upper()}-"
        f"{now:%Y%m%d-%H%M%S}-"
        f"{uuid4().hex[:6]}"
    )


# =============================================================
# Formatting
# =============================================================


def _currency_symbol(
    currency: Currency,
) -> str:

    symbols = {
        Currency.EUR: "€",
        Currency.USD: "$",
        Currency.GBP: "£",
        Currency.JPY: "¥",
        Currency.CHF: "CHF ",
        Currency.CAD: "C$",
    }

    return symbols.get(
        currency,
        f"{currency.value} ",
    )


def _format_money(
    value: float,
    currency: Currency,
) -> str:

    return (
        f"{_currency_symbol(currency)}"
        f"{value:,.2f}"
    )


def _format_optional(
    value: object,
) -> str:

    return (
        "UNKNOWN"
        if value is None
        else str(value)
    )


def _format_currency(
    value: Currency | None,
) -> str:

    return (
        "UNKNOWN"
        if value is None
        else value.value
    )


def _format_bool(
    value: bool | None,
) -> str:

    if value is True:
        return "YES"

    if value is False:
        return "NO"

    return "UNKNOWN"


# =============================================================
# Account State
# =============================================================


def account_set(
    store: Stage3Store,
) -> None:

    print(
        "\n=== SET CIO ACCOUNT STATE ===\n"
    )

    print(
        "Leave optional fields blank "
        "when no limit is configured.\n"
    )

    eur_available = _prompt_float(
        "Available EUR cash: €",
        minimum=0,
    )

    eur_reserve = _prompt_float(
        "EUR reserve: €",
        minimum=0,
    )

    usd_available = _prompt_float(
        "Available USD cash: $",
        minimum=0,
    )

    usd_reserve = _prompt_float(
        "USD reserve: $",
        minimum=0,
    )

    account_equity_eur = _prompt_float(
        "Account equity / portfolio NAV EUR "
        "[optional]: €",
        optional=True,
        minimum=0.000001,
    )

    max_trade_loss = _prompt_float(
        "Max loss per trade EUR "
        "[optional]: €",
        optional=True,
        minimum=0,
    )

    max_position_weight = (
        _prompt_float(
            "Max position weight % "
            "[optional]: ",
            optional=True,
            minimum=0,
        )
    )

    max_portfolio_gross_exposure = (
        _prompt_float(
            "Max portfolio gross exposure % "
            "[optional]: ",
            optional=True,
            minimum=0,
        )
    )

    max_cio_deployable = (
        _prompt_float(
            "Max CIO deployable % "
            "[optional]: ",
            optional=True,
            minimum=0,
        )
    )

    max_portfolio_beta = (
        _prompt_float(
            "Max portfolio beta "
            "[optional]: ",
            optional=True,
        )
    )

    max_var = _prompt_float(
        "Max VaR 95% 1D EUR "
        "[optional]: €",
        optional=True,
        minimum=0,
    )

    notes = (
        input(
            "Notes [optional]: "
        ).strip()
        or None
    )

    now = datetime.now(
        timezone.utc
    )

    state = AccountState(
        account_state_id=(
            _new_account_state_id(
                now
            )
        ),
        timestamp=now,
        cash=[
            CurrencyCash(
                currency=Currency.EUR,
                available=eur_available,
                reserve=eur_reserve,
            ),
            CurrencyCash(
                currency=Currency.USD,
                available=usd_available,
                reserve=usd_reserve,
            ),
        ],
        account_equity_eur=(
            account_equity_eur
        ),
        constraints=RiskConstraints(
            max_trade_loss_eur=(
                max_trade_loss
            ),
            max_position_weight_pct=(
                max_position_weight
            ),
            max_portfolio_gross_exposure_pct=(
                max_portfolio_gross_exposure
            ),
            max_cio_deployable_pct=(
                max_cio_deployable
            ),
            max_portfolio_beta=(
                max_portfolio_beta
            ),
            max_var_95_1d_eur=(
                max_var
            ),
            min_cash_reserve_eur=(
                eur_reserve
            ),
            min_cash_reserve_usd=(
                usd_reserve
            ),
        ),
        source=DataSource.OPERATOR,
        notes=notes,
    )

    store.save_account_state(
        state
    )

    print(
        "\nAccount State saved: "
        f"{state.account_state_id}"
    )


def account_show(
    store: Stage3Store,
) -> None:

    state = (
        store.get_latest_account_state()
    )

    if state is None:

        print(
            "No Account State configured."
        )

        return

    print(
        "\n=== CIO ACCOUNT STATE ===\n"
    )

    print(
        f"ID:        "
        f"{state.account_state_id}"
    )

    print(
        f"Timestamp: "
        f"{state.timestamp.isoformat()}"
    )

    print(
        f"Source:    "
        f"{state.source.value}"
    )

    print(
        "NAV/Equity: "
        + (
            f"€{state.account_equity_eur:,.2f}"
            if state.account_equity_eur is not None
            else "UNKNOWN"
        )
        + "\n"
    )

    header = (
        f"{'Currency':<10}"
        f"{'Available':>16}"
        f"{'Reserve':>16}"
        f"{'Deployable':>16}"
        f"{'Buying Power':>16}"
    )

    print(header)
    print("-" * len(header))

    for item in state.cash:

        deployable = (
            item.available
            - item.reserve
        )

        buying_power = (
            "UNKNOWN"
            if item.buying_power is None
            else _format_money(
                item.buying_power,
                item.currency,
            )
        )

        print(
            f"{item.currency.value:<10}"
            f"{_format_money(item.available, item.currency):>16}"
            f"{_format_money(item.reserve, item.currency):>16}"
            f"{_format_money(deployable, item.currency):>16}"
            f"{buying_power:>16}"
        )

    constraints = (
        state.constraints
    )

    print(
        "\nRisk constraints"
    )

    print(
        "-" * 54
    )

    print(
        "Max loss / trade EUR:       "
        f"{_format_optional(constraints.max_trade_loss_eur)}"
    )

    print(
        "Max position weight %:      "
        f"{_format_optional(constraints.max_position_weight_pct)}"
    )

    print(
        "Max portfolio gross exposure %: "
        f"{_format_optional(constraints.max_portfolio_gross_exposure_pct)}"
    )

    print(
        "Max CIO deployable %:            "
        f"{_format_optional(constraints.max_cio_deployable_pct)}"
    )

    print(
        "Max portfolio beta:         "
        f"{_format_optional(constraints.max_portfolio_beta)}"
    )

    print(
        "Max VaR 95% 1D EUR:         "
        f"{_format_optional(constraints.max_var_95_1d_eur)}"
    )

    if state.notes:

        print(
            f"\nNotes: "
            f"{state.notes}"
        )


# =============================================================
# Fineco Instrument Cache
# =============================================================


def instrument_add(
    store: Stage3Store,
) -> None:

    print(
        "\n=== ADD FINECO INSTRUMENT ===\n"
    )

    print(
        "One record represents ONE "
        "specific Fineco operating mode.\n"
    )

    print(
        "Examples:\n"
        "  GOOGL.O / MARGIN / OVERNIGHT / X10\n"
        "  GOOGL.O / MARGIN / INTRADAY / X20\n"
        "  GGLL.O / ETF / ORDINARY / embedded 2X\n"
    )

    print(
        "Use ? or ENTER for unknown "
        "boolean fields.\n"
        "Leave optional fields blank "
        "when the value is not known.\n"
    )

    # ---------------------------------------------------------
    # Identity
    # ---------------------------------------------------------

    underlying = (
        input(
            "Underlying ticker: "
        )
        .strip()
        .upper()
    )

    if not underlying:

        raise ValueError(
            "Underlying ticker is required"
        )

    description = (
        input(
            "Description [optional]: "
        ).strip()
        or None
    )

    instrument_type = (
        InstrumentType(
            _prompt_choice(
                "Instrument type",
                [
                    item.value
                    for item
                    in InstrumentType
                ],
            )
        )
    )

    direct_types = {
        InstrumentType.ORDINARY,
        InstrumentType.MARGIN,
        InstrumentType.CFD,
        InstrumentType.CFDC,
    }

    # ---------------------------------------------------------
    # Semantic relationship
    # ---------------------------------------------------------

    if instrument_type in direct_types:

        reference_underlying = (
            underlying
        )

        exposure_relationship = (
            ExposureRelationship.DIRECT
        )

    else:

        reference_underlying = (
            input(
                "Reference underlying "
                f"[{underlying}]: "
            )
            .strip()
            .upper()
            or underlying
        )

        if (
            instrument_type
            == InstrumentType.CERTIFICATE
        ):

            exposure_relationship = (
                ExposureRelationship.STRUCTURED
            )

            print(
                "Exposure relationship: "
                "STRUCTURED "
                "(automatic for certificates)"
            )

        else:

            exposure_relationship = (
                ExposureRelationship(
                    _prompt_choice(
                        "Exposure relationship",
                        [
                            item.value
                            for item
                            in ExposureRelationship
                        ],
                    )
                )
            )

    # ---------------------------------------------------------
    # Trading mode
    # ---------------------------------------------------------

    trading_mode = (
        TradingMode(
            _prompt_choice(
                "Trading mode",
                [
                    item.value
                    for item
                    in TradingMode
                ],
            )
        )
    )

    fineco_symbol = (
        input(
            "Fineco symbol/code "
            "[optional]: "
        ).strip()
        or None
    )

    market = (
        input(
            "Fineco market "
            "(NASDAQ/CFD/CFDC/ETLX/etc.) "
            "[optional]: "
        )
        .strip()
        .upper()
        or None
    )

    # ---------------------------------------------------------
    # Currency
    # ---------------------------------------------------------

    quote_currency = (
        Currency(
            _prompt_choice(
                "Quote currency",
                [
                    item.value
                    for item
                    in Currency
                ],
            )
        )
    )

    settlement_raw = (
        _prompt_optional_choice(
            "Settlement currency",
            [
                item.value
                for item
                in Currency
            ],
        )
    )

    settlement_currency = (
        Currency(
            settlement_raw
        )
        if settlement_raw
        else None
    )

    margin_raw = (
        _prompt_optional_choice(
            "Margin currency",
            [
                item.value
                for item
                in Currency
            ],
        )
    )

    margin_currency = (
        Currency(
            margin_raw
        )
        if margin_raw
        else None
    )

    # ---------------------------------------------------------
    # Direction availability
    # ---------------------------------------------------------

    long_available = (
        _prompt_bool_optional(
            "LONG available"
        )
    )

    short_available = (
        _prompt_bool_optional(
            "SHORT available"
        )
    )

    intraday_available = (
        _prompt_bool_optional(
            "Intraday available"
        )
    )

    overnight_available = (
        _prompt_bool_optional(
            "Overnight available"
        )
    )

    # ---------------------------------------------------------
    # Broker leverage
    # ---------------------------------------------------------

    broker_leverage = (
        _prompt_float(
            "Broker leverage "
            "[optional]: ",
            optional=True,
            minimum=0.000001,
        )
    )

    # ---------------------------------------------------------
    # Embedded product leverage
    #
    # Direct instruments always have 1X embedded exposure.
    # ETF/ETN/etc may embed 2X, 3X, etc.
    # ---------------------------------------------------------



    if instrument_type in direct_types:

        embedded_leverage = 1.0

    elif instrument_type == InstrumentType.CERTIFICATE:

        embedded_leverage = _prompt_float(
            "Embedded product leverage "
            "[optional]: ",
            optional=True,
            minimum=0.000001,
        )

    else:

        embedded_leverage = _prompt_float(
            "Embedded product leverage "
            "[optional, default 1]: ",
            optional=True,
            minimum=0.000001,
        )

    if embedded_leverage is None:
        embedded_leverage = 1.0

    # ---------------------------------------------------------
    # Margin
    # ---------------------------------------------------------

    margin_pct = (
        _prompt_float(
            "Margin % [optional]: ",
            optional=True,
            minimum=0,
        )
    )

    # ---------------------------------------------------------
    # Static product characteristics
    # ---------------------------------------------------------

    tick_size = (
        _prompt_float(
            "Tick size [optional]: ",
            optional=True,
            minimum=0.00000001,
        )
    )

    tick_currency = None

    if tick_size is not None:

        tick_currency_raw = (
            _prompt_optional_choice(
                "Tick currency",
                [
                    item.value
                    for item
                    in Currency
                ],
            )
        )

        tick_currency = (
            Currency(
                tick_currency_raw
            )
            if tick_currency_raw
            else None
        )

    commission = (
        _prompt_float(
            "Commission [optional]: ",
            optional=True,
            minimum=0,
        )
    )

    overnight_financing = (
        _prompt_float(
            "Overnight financing % "
            "[optional]: ",
            optional=True,
        )
    )

    # ---------------------------------------------------------
    # Certificate-specific data
    # ---------------------------------------------------------

    certificate_terms = None

    if (
        instrument_type
        == InstrumentType.CERTIFICATE
    ):

        certificate_terms = (
            _read_certificate_terms_new()
        )

    notes = (
        input(
            "Notes [optional]: "
        ).strip()
        or None
    )

    # ---------------------------------------------------------
    # Create canonical object
    # ---------------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    instrument = FinecoInstrument(

        instrument_id=(
            _new_instrument_id(
                underlying,
                instrument_type,
                trading_mode,
                broker_leverage,
            )
        ),

        underlying=underlying,

        reference_underlying=(
            reference_underlying
        ),

        exposure_relationship=(
            exposure_relationship
        ),

        description=description,

        instrument_type=(
            instrument_type
        ),

        trading_mode=(
            trading_mode
        ),

        fineco_symbol=(
            fineco_symbol
        ),

        market=market,

        quote_currency=(
            quote_currency
        ),

        settlement_currency=(
            settlement_currency
        ),

        margin_currency=(
            margin_currency
        ),

        long_available=(
            long_available
        ),

        short_available=(
            short_available
        ),

        intraday_available=(
            intraday_available
        ),

        overnight_available=(
            overnight_available
        ),

        broker_leverage=(
            broker_leverage
        ),

        embedded_leverage=(
            embedded_leverage
        ),

        margin_pct=(
            margin_pct
        ),

        tick_size=(
            tick_size
        ),

        tick_currency=(
            tick_currency
        ),

        commission=(
            commission
        ),

        overnight_financing_pct=(
            overnight_financing
        ),

        certificate_terms=(
            certificate_terms
        ),

        market_snapshot=None,

        last_confirmed=now,

        source=(
            DataSource.OPERATOR
        ),

        cache_status=(
            CacheStatus.CURRENT
        ),

        notes=notes,
    )

    store.save_fineco_instrument(
        instrument
    )

    print(
        "\nFineco instrument saved: "
        f"{instrument.instrument_id}"
    )


# =============================================================
# Fineco Instrument Edit
# =============================================================


def instrument_edit(
    store: Stage3Store,
    instrument_id: str,
) -> None:

    current = (
        store.get_fineco_instrument(
            instrument_id
        )
    )

    if current is None:

        print(
            "Fineco instrument not found: "
            f"{instrument_id}"
        )

        return

    print(
        "\n=== EDIT FINECO INSTRUMENT ===\n"
    )

    print(
        f"Instrument ID: "
        f"{current.instrument_id}\n"
    )

    print(
        "Press ENTER to keep the current value.\n"
        "Use '-' to clear an optional value.\n"
        "For YES/NO fields use y, n or ?.\n"
    )

    # ---------------------------------------------------------
    # Identity
    # ---------------------------------------------------------

    underlying = (
        _prompt_text_default(
            "Underlying ticker",
            current.underlying,
            required=True,
        )
    )

    assert underlying is not None

    underlying = (
        underlying
        .strip()
        .upper()
    )

    instrument_type = (
        InstrumentType(
            _prompt_choice_default(
                "Instrument type",
                [
                    item.value
                    for item
                    in InstrumentType
                ],
                current.instrument_type.value,
            )
        )
    )

    direct_types = {
        InstrumentType.ORDINARY,
        InstrumentType.MARGIN,
        InstrumentType.CFD,
        InstrumentType.CFDC,
    }

    # ---------------------------------------------------------
    # Semantic relationship
    # ---------------------------------------------------------

    if instrument_type in direct_types:

        reference_underlying = (
            underlying
        )

        exposure_relationship = (
            ExposureRelationship.DIRECT
        )

    else:

        reference_underlying = (
            _prompt_text_default(
                "Reference underlying",
                (
                    current.reference_underlying
                    or current.underlying
                ),
                required=True,
            )
        )

        assert (
            reference_underlying
            is not None
        )

        reference_underlying = (
            reference_underlying
            .strip()
            .upper()
        )

        if (
            instrument_type
            == InstrumentType.CERTIFICATE
        ):

            exposure_relationship = (
                ExposureRelationship.STRUCTURED
            )

            print(
                "Exposure relationship: "
                "STRUCTURED "
                "(automatic for certificates)"
            )

        else:

            exposure_relationship = (
                ExposureRelationship(
                    _prompt_choice_default(
                        "Exposure relationship",
                        [
                            item.value
                            for item
                            in ExposureRelationship
                        ],
                        current.exposure_relationship.value,
                    )
                )
            )

    description = (
        _prompt_text_default(
            "Description",
            current.description,
        )
    )

    trading_mode = (
        TradingMode(
            _prompt_choice_default(
                "Trading mode",
                [
                    item.value
                    for item
                    in TradingMode
                ],
                current.trading_mode.value,
            )
        )
    )

    fineco_symbol = (
        _prompt_text_default(
            "Fineco symbol/code",
            current.fineco_symbol,
        )
    )

    market = (
        _prompt_text_default(
            "Fineco market",
            current.market,
        )
    )

    if market is not None:

        market = market.upper()

    # ---------------------------------------------------------
    # Currency
    # ---------------------------------------------------------

    quote_currency = (
        Currency(
            _prompt_choice_default(
                "Quote currency",
                [
                    item.value
                    for item
                    in Currency
                ],
                current.quote_currency.value,
            )
        )
    )

    settlement_raw = (
        _prompt_optional_choice_default(
            "Settlement currency",
            [
                item.value
                for item
                in Currency
            ],
            (
                current.settlement_currency.value
                if current.settlement_currency
                else None
            ),
        )
    )

    settlement_currency = (
        Currency(
            settlement_raw
        )
        if settlement_raw
        else None
    )

    margin_raw = (
        _prompt_optional_choice_default(
            "Margin currency",
            [
                item.value
                for item
                in Currency
            ],
            (
                current.margin_currency.value
                if current.margin_currency
                else None
            ),
        )
    )

    margin_currency = (
        Currency(
            margin_raw
        )
        if margin_raw
        else None
    )

    # ---------------------------------------------------------
    # Direction availability
    # ---------------------------------------------------------

    long_available = (
        _prompt_bool_default(
            "LONG available",
            current.long_available,
        )
    )

    short_available = (
        _prompt_bool_default(
            "SHORT available",
            current.short_available,
        )
    )

    intraday_available = (
        _prompt_bool_default(
            "Intraday available",
            current.intraday_available,
        )
    )

    overnight_available = (
        _prompt_bool_default(
            "Overnight available",
            current.overnight_available,
        )
    )

    # ---------------------------------------------------------
    # Broker leverage
    # ---------------------------------------------------------

    broker_leverage = (
        _prompt_float_default(
            "Broker leverage",
            current.broker_leverage,
            minimum=0.000001,
        )
    )

    # ---------------------------------------------------------
    # Embedded product leverage
    # ---------------------------------------------------------

    if instrument_type in direct_types:

        embedded_leverage = 1.0

    else:

        embedded_leverage = (
            _prompt_float_default(
                "Embedded product leverage",
                current.embedded_leverage,
                minimum=0.000001,
            )
        )

        if embedded_leverage is None:

            embedded_leverage = 1.0

    # ---------------------------------------------------------
    # Margin
    # ---------------------------------------------------------

    margin_pct = (
        _prompt_float_default(
            "Margin %",
            current.margin_pct,
            minimum=0,
        )
    )

    # ---------------------------------------------------------
    # Tick
    # ---------------------------------------------------------

    tick_size = (
        _prompt_float_default(
            "Tick size",
            current.tick_size,
            minimum=0.00000001,
        )
    )

    if tick_size is None:

        tick_currency = None

    else:

        tick_currency_raw = (
            _prompt_optional_choice_default(
                "Tick currency",
                [
                    item.value
                    for item
                    in Currency
                ],
                (
                    current.tick_currency.value
                    if current.tick_currency
                    else None
                ),
            )
        )

        tick_currency = (
            Currency(
                tick_currency_raw
            )
            if tick_currency_raw
            else None
        )

    # ---------------------------------------------------------
    # Costs
    # ---------------------------------------------------------

    commission = (
        _prompt_float_default(
            "Commission",
            current.commission,
            minimum=0,
        )
    )

    overnight_financing = (
        _prompt_float_default(
            "Overnight financing %",
            current.overnight_financing_pct,
        )
    )

    # ---------------------------------------------------------
    # Certificate terms
    # ---------------------------------------------------------

    if (
        instrument_type
        == InstrumentType.CERTIFICATE
    ):

        certificate_terms = (
            _read_certificate_terms_edit(
                current.certificate_terms
            )
        )

    else:

        certificate_terms = None

    notes = (
        _prompt_text_default(
            "Notes",
            current.notes,
        )
    )

    # ---------------------------------------------------------
    # Rebuild canonical object
    # ---------------------------------------------------------

    data = (
        current.model_dump()
    )

    data.update(
        {
            "underlying":
                underlying,

            "reference_underlying":
                reference_underlying,

            "exposure_relationship":
                exposure_relationship,

            "description":
                description,

            "instrument_type":
                instrument_type,

            "trading_mode":
                trading_mode,

            "fineco_symbol":
                fineco_symbol,

            "market":
                market,

            "quote_currency":
                quote_currency,

            "settlement_currency":
                settlement_currency,

            "margin_currency":
                margin_currency,

            "long_available":
                long_available,

            "short_available":
                short_available,

            "intraday_available":
                intraday_available,

            "overnight_available":
                overnight_available,

            "broker_leverage":
                broker_leverage,

            "embedded_leverage":
                embedded_leverage,

            "margin_pct":
                margin_pct,

            "tick_size":
                tick_size,

            "tick_currency":
                tick_currency,

            "commission":
                commission,

            "overnight_financing_pct":
                overnight_financing,

            "certificate_terms":
                certificate_terms,

            "last_confirmed":
                datetime.now(
                    timezone.utc
                ),

            "source":
                DataSource.OPERATOR,

            "cache_status":
                CacheStatus.CURRENT,

            "notes":
                notes,
        }
    )

    updated = (
        FinecoInstrument.model_validate(
            data
        )
    )

    store.save_fineco_instrument(
        updated
    )

    print(
        "\nFineco instrument updated: "
        f"{updated.instrument_id}"
    )


# =============================================================
# Fineco Instrument Delete
# =============================================================


def instrument_delete(
    store: Stage3Store,
    instrument_id: str,
) -> None:

    instrument = (
        store.get_fineco_instrument(
            instrument_id
        )
    )

    if instrument is None:

        print(
            "Fineco instrument not found: "
            f"{instrument_id}"
        )

        return

    print(
        "\n=== DELETE FINECO INSTRUMENT ===\n"
    )

    print(
        f"ID:         "
        f"{instrument.instrument_id}"
    )

    print(
        f"Underlying: "
        f"{instrument.underlying}"
    )

    print(
        f"Reference:  "
        f"{instrument.effective_reference_underlying}"
    )

    print(
        f"Relation:   "
        f"{instrument.exposure_relationship.value}"
    )

    print(
        f"Type:       "
        f"{instrument.instrument_type.value}"
    )

    print(
        f"Mode:       "
        f"{instrument.trading_mode.value}"
    )

    print(
        "Broker leverage:   "
        f"{_format_optional(instrument.broker_leverage)}"
    )

    print(
        "Embedded leverage: "
        f"{_format_optional(instrument.embedded_leverage)}"
    )

    confirmation = (
        input(
            "\nDelete this instrument? "
            "[y/N]: "
        )
        .strip()
        .lower()
    )

    if confirmation not in {
        "y",
        "yes",
        "s",
        "si",
        "sì",
    }:

        print(
            "Delete cancelled."
        )

        return

    deleted = (
        store.delete_fineco_instrument(
            instrument_id
        )
    )

    if deleted:

        print(
            "Fineco instrument deleted: "
            f"{instrument_id}"
        )

    else:

        print(
            "Instrument was not deleted."
        )


# =============================================================
# Instrument output
# =============================================================


def _print_instrument_table(
    instruments: list[FinecoInstrument],
) -> None:

    if not instruments:

        print(
            "No Fineco instruments found."
        )

        return

    header = (
        f"{'Underlying':<11}"
        f"{'Ref':<11}"
        f"{'Relation':<16}"
        f"{'Type':<12}"
        f"{'Mode':<16}"
        f"{'Market':<10}"
        f"{'Quote':<8}"
        f"{'Settle':<8}"
        f"{'LONG':<9}"
        f"{'SHORT':<9}"
        f"{'BrokerLev':>10}"
        f"{'EmbedLev':>10}"
        f"{'Margin%':>10}"
        f"{'Status':>10}"
    )

    print(
        header
    )

    print(
        "-" * len(header)
    )

    for item in instruments:

        broker_leverage = (
            "UNKNOWN"
            if item.broker_leverage is None
            else (
                f"{item.broker_leverage:g}x"
            )
        )

        embedded_leverage = (
            "UNKNOWN"
            if item.embedded_leverage is None
            else (
                f"{item.embedded_leverage:g}x"
            )
        )

        margin = (
            "UNKNOWN"
            if item.margin_pct is None
            else (
                f"{item.margin_pct:g}%"
            )
        )

        market = (
            item.market
            if item.market
            else "UNKNOWN"
        )

        print(
            f"{item.underlying:<11}"
            f"{item.effective_reference_underlying:<11}"
            f"{item.exposure_relationship.value:<16}"
            f"{item.instrument_type.value:<12}"
            f"{item.trading_mode.value:<16}"
            f"{market:<10}"
            f"{item.quote_currency.value:<8}"
            f"{_format_currency(item.settlement_currency):<8}"
            f"{_format_bool(item.long_available):<9}"
            f"{_format_bool(item.short_available):<9}"
            f"{broker_leverage:>10}"
            f"{embedded_leverage:>10}"
            f"{margin:>10}"
            f"{item.cache_status.value:>10}"
        )

        details: list[str] = []

        details.append(
            f"instrument_id="
            f"{item.instrument_id}"
        )

        if item.reference_underlying:

            details.append(
                "reference_underlying="
                f"{item.reference_underlying}"
            )

        if item.description:

            details.append(
                f"description="
                f"{item.description}"
            )

        if item.fineco_symbol:

            details.append(
                f"fineco_symbol="
                f"{item.fineco_symbol}"
            )

        if item.margin_currency:

            details.append(
                f"margin_currency="
                f"{item.margin_currency.value}"
            )

        if item.tick_size is not None:

            tick_description = (
                f"tick="
                f"{item.tick_size:g}"
            )

            if item.tick_currency:

                tick_description += (
                    " "
                    f"{item.tick_currency.value}"
                )

            details.append(
                tick_description
            )

        if item.commission is not None:

            details.append(
                f"commission="
                f"{item.commission:g}"
            )

        if (
            item.overnight_financing_pct
            is not None
        ):

            details.append(
                "overnight_financing="
                f"{item.overnight_financing_pct:g}%"
            )

        if item.last_confirmed:

            details.append(
                "confirmed="
                f"{item.last_confirmed.date().isoformat()}"
            )

        # -----------------------------------------------------
        # Dynamic market snapshot
        # -----------------------------------------------------

        if item.market_snapshot:

            snapshot = (
                item.market_snapshot
            )

            if snapshot.bid is not None:

                details.append(
                    f"bid="
                    f"{snapshot.bid:g}"
                )

            if snapshot.ask is not None:

                details.append(
                    f"ask="
                    f"{snapshot.ask:g}"
                )

            if snapshot.spread is not None:

                details.append(
                    f"spread="
                    f"{snapshot.spread:g}"
                )

            details.append(
                "market_observed="
                f"{snapshot.observed_at.isoformat()}"
            )

        # -----------------------------------------------------
        # Certificate terms
        # -----------------------------------------------------

        if item.certificate_terms:

            terms = (
                item.certificate_terms
            )

            if terms.issuer:

                details.append(
                    f"issuer="
                    f"{terms.issuer}"
                )

            if terms.certificate_type:

                details.append(
                    "certificate_type="
                    f"{terms.certificate_type}"
                )

            if terms.maturity_date:

                details.append(
                    "maturity="
                    f"{terms.maturity_date.isoformat()}"
                )

            if terms.strike is not None:

                details.append(
                    f"strike="
                    f"{terms.strike:g}"
                )

            if terms.barrier is not None:

                details.append(
                    f"barrier="
                    f"{terms.barrier:g}"
                )

            if terms.barrier_pct is not None:

                details.append(
                    "barrier_pct="
                    f"{terms.barrier_pct:g}%"
                )

            if terms.ratio is not None:

                details.append(
                    f"ratio="
                    f"{terms.ratio:g}"
                )

            if terms.isin:

                details.append(
                    f"isin="
                    f"{terms.isin}"
                )

        if item.notes:

            details.append(
                f"notes="
                f"{item.notes}"
            )

        if details:

            print(
                "  "
                + " | ".join(
                    details
                )
            )


def instruments_show(
    store: Stage3Store,
    underlying: str,
) -> None:

    ticker = (
        underlying
        .strip()
        .upper()
    )

    instruments = (
        store.find_fineco_instruments(
            ticker
        )
    )

    print(
        "\n=== FINECO INSTRUMENTS: "
        f"{ticker} ===\n"
    )

    if not instruments:

        print(
            "No Fineco instruments "
            f"known for {ticker}."
        )

        return

    _print_instrument_table(
        instruments
    )


def instruments_list(
    store: Stage3Store,
) -> None:

    print(
        "\n=== FINECO INSTRUMENT "
        "CACHE ===\n"
    )

    _print_instrument_table(
        store.list_fineco_instruments()
    )

# =============================================================
# Trade Opportunities
# =============================================================


def _print_opportunity(
    opportunity: TradeOpportunity,
) -> None:

    print(
        "\n=== CIO TRADE OPPORTUNITY ===\n"
    )

    print(
        f"ID:          "
        f"{opportunity.opportunity_id}"
    )

    print(
        f"Snapshot:    "
        f"{opportunity.snapshot_id}"
    )

    print(
        f"Ticker:      "
        f"{opportunity.ticker}"
    )

    print(
        f"Direction:   "
        f"{opportunity.direction.value}"
    )

    print(
        f"Horizon:     "
        f"{opportunity.horizon.value}"
    )

    print(
        f"Confidence:  "
        f"{opportunity.confidence:.0%}"
    )

    print(
        f"Status:      "
        f"{opportunity.status.value}"
    )

    if (
        opportunity.expected_holding_min_days
        is not None
        or opportunity.expected_holding_max_days
        is not None
    ):

        minimum = (
            str(
                opportunity.expected_holding_min_days
            )
            if opportunity.expected_holding_min_days
            is not None
            else "?"
        )

        maximum = (
            str(
                opportunity.expected_holding_max_days
            )
            if opportunity.expected_holding_max_days
            is not None
            else "?"
        )

        print(
            f"Holding:     "
            f"{minimum}-{maximum} days"
        )

    print(
        "\nBroker instruments"
    )

    print(
        "-" * 54
    )

    print(
        "Required:    "
        f"{_format_bool(opportunity.broker_instruments_required)}"
    )

    print(
        "Available:   "
        f"{_format_bool(opportunity.broker_instruments_available)}"
    )

    print(
        "Known count: "
        f"{_format_optional(opportunity.broker_instrument_count)}"
    )

    print(
        "\nThesis"
    )

    print(
        "-" * 54
    )

    print(
        opportunity.thesis
    )

    if opportunity.catalyst:

        print(
            "\nCatalyst"
        )

        print(
            "-" * 54
        )

        print(
            opportunity.catalyst
        )

    if opportunity.key_risks:

        print(
            "\nKey risks"
        )

        print(
            "-" * 54
        )

        for risk in opportunity.key_risks:

            print(
                f"- {risk}"
            )

    if opportunity.rejection_reason:

        print(
            "\nRejection reason: "
            f"{opportunity.rejection_reason}"
        )

    if opportunity.expiry_reason:

        print(
            "\nExpiry reason: "
            f"{opportunity.expiry_reason}"
        )

    if opportunity.notes:

        print(
            "\nNotes: "
            f"{opportunity.notes}"
        )

    if (
        opportunity.status
        == OpportunityStatus
        .WAITING_FOR_BROKER_INSTRUMENTS
    ):

        print(
            "\nACTION REQUIRED"
        )

        print(
            "-" * 54
        )

        print(
            "Provide Fineco screenshots for "
            f"{opportunity.ticker} and bulk-import "
            "the available instruments."
        )


def opportunities_list(
    store: Stage3Store,
    status: str | None = None,
) -> None:

    status_enum = (
        OpportunityStatus(status)
        if status
        else None
    )

    opportunities = (
        store.list_trade_opportunities(
            status=status_enum
        )
    )

    print(
        "\n=== CIO TRADE OPPORTUNITIES ===\n"
    )

    if not opportunities:

        print(
            "No trade opportunities found."
        )

        return

    header = (
        f"{'ID':<34}"
        f"{'Ticker':<10}"
        f"{'Direction':<11}"
        f"{'Horizon':<12}"
        f"{'Conf':>8}"
        f"{'Broker':>10}"
        f"{'Count':>8}"
        f"  {'Status'}"
    )

    print(
        header
    )

    print(
        "-" * len(header)
    )

    for item in opportunities:

        broker = (
            _format_bool(
                item.broker_instruments_available
            )
        )

        count = (
            "?"
            if item.broker_instrument_count is None
            else str(
                item.broker_instrument_count
            )
        )

        print(
            f"{item.opportunity_id:<34}"
            f"{item.ticker:<10}"
            f"{item.direction.value:<11}"
            f"{item.horizon.value:<12}"
            f"{item.confidence:>7.0%}"
            f"{broker:>10}"
            f"{count:>8}"
            f"  {item.status.value}"
        )


def opportunity_show(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    opportunity = (
        store.get_trade_opportunity(
            opportunity_id
        )
    )

    if opportunity is None:

        print(
            "Trade opportunity not found: "
            f"{opportunity_id}"
        )

        return

    _print_opportunity(
        opportunity
    )


def opportunity_add(
    store: Stage3Store,
) -> None:

    print(
        "\n=== ADD CIO TRADE OPPORTUNITY ===\n"
    )

    ticker = (
        input(
            "Ticker: "
        )
        .strip()
        .upper()
    )

    if not ticker:

        raise ValueError(
            "Ticker is required"
        )

    direction = Direction(
        _prompt_choice(
            "Direction",
            [
                item.value
                for item
                in Direction
            ],
        )
    )

    horizon = TradingHorizon(
        _prompt_choice(
            "Trading horizon",
            [
                item.value
                for item
                in TradingHorizon
            ],
        )
    )

    confidence_pct = _prompt_float(
        "Confidence %: ",
        minimum=0,
    )

    assert confidence_pct is not None

    if confidence_pct > 100:

        raise ValueError(
            "Confidence cannot exceed 100%"
        )

    confidence = (
        confidence_pct / 100.0
    )

    holding_min = _prompt_float(
        "Expected holding min days "
        "[optional]: ",
        optional=True,
        minimum=0,
    )

    holding_max = _prompt_float(
        "Expected holding max days "
        "[optional]: ",
        optional=True,
        minimum=0,
    )

    thesis = (
        input(
            "Thesis: "
        ).strip()
    )

    if not thesis:

        raise ValueError(
            "Thesis is required"
        )

    catalyst = (
        input(
            "Catalyst [optional]: "
        ).strip()
        or None
    )

    latest_snapshot = (
        store.get_latest_portfolio_snapshot()
    )

    default_snapshot_id = (
        latest_snapshot.snapshot_id
        if latest_snapshot is not None
        else None
    )

    if default_snapshot_id is not None:

        snapshot_id = (
            input(
                f"Snapshot ID "
                f"[{default_snapshot_id}]: "
            )
            .strip()
            or default_snapshot_id
        )

    else:

        # Manual/testing opportunities are allowed even when the
        # analytical PortfolioSnapshot pipeline has not yet persisted
        # a snapshot in the Stage 3 database.
        manual_snapshot_id = (
            "SNAP-MANUAL-"
            f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-"
            f"{uuid4().hex[:6]}"
        )

        print(
            "\nNo PortfolioSnapshot is currently persisted "
            "in the Stage 3 database."
        )

        print(
            "Using synthetic snapshot reference for this "
            "manual/test opportunity:"
        )

        print(
            f"  {manual_snapshot_id}\n"
        )

        snapshot_id = manual_snapshot_id

    target_exposure = _prompt_float(
        "Target exposure EUR "
        "[optional]: €",
        optional=True,
        minimum=0.000001,
    )

    max_loss = _prompt_float(
        "Max intended loss EUR "
        "[optional]: €",
        optional=True,
        minimum=0,
    )

    notes = (
        input(
            "Notes [optional]: "
        ).strip()
        or None
    )

    now = datetime.now(
        timezone.utc
    )

    opportunity = TradeOpportunity(
        opportunity_id=(
            _new_opportunity_id(
                ticker,
                now,
            )
        ),
        snapshot_id=snapshot_id,
        created_at=now,
        updated_at=now,
        ticker=ticker,
        direction=direction,
        horizon=horizon,
        expected_holding_min_days=(
            int(holding_min)
            if holding_min is not None
            else None
        ),
        expected_holding_max_days=(
            int(holding_max)
            if holding_max is not None
            else None
        ),
        confidence=confidence,
        target_exposure_eur=(
            target_exposure
        ),
        max_intended_loss_eur=(
            max_loss
        ),
        thesis=thesis,
        catalyst=catalyst,
        key_risks=[],
        evidence_ids=[],
        status=(
            OpportunityStatus.DISCOVERED
        ),
        broker_instruments_required=True,
        broker_instruments_available=None,
        broker_instrument_count=None,
        rejection_reason=None,
        expiry_reason=None,
        notes=notes,
    )

    store.save_trade_opportunity(
        opportunity
    )

    # Immediately check the Fineco cache.
    updated = (
        store.refresh_trade_opportunity_broker_state(
            opportunity.opportunity_id
        )
    )

    print(
        "\nTrade opportunity saved."
    )

    if updated is not None:

        _print_opportunity(
            updated
        )


def opportunity_refresh(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    updated = (
        store.refresh_trade_opportunity_broker_state(
            opportunity_id
        )
    )

    if updated is None:

        print(
            "Trade opportunity not found: "
            f"{opportunity_id}"
        )

        return

    print(
        "\nOpportunity refreshed."
    )

    _print_opportunity(
        updated
    )


def opportunities_refresh_waiting(
    store: Stage3Store,
) -> None:

    refreshed = (
        store.refresh_waiting_trade_opportunities()
    )

    print(
        "\n=== REFRESH WAITING OPPORTUNITIES ===\n"
    )

    if not refreshed:

        print(
            "No waiting opportunities found."
        )

        return

    ready = [
        item
        for item in refreshed
        if (
            item.status
            == OpportunityStatus
            .READY_FOR_INSTRUMENT_SELECTION
        )
    ]

    still_waiting = [
        item
        for item in refreshed
        if (
            item.status
            == OpportunityStatus
            .WAITING_FOR_BROKER_INSTRUMENTS
        )
    ]

    print(
        f"Checked:       {len(refreshed)}"
    )

    print(
        f"Now READY:     {len(ready)}"
    )

    print(
        f"Still waiting: {len(still_waiting)}"
    )

    if ready:

        print(
            "\nREADY FOR INSTRUMENT SELECTION"
        )

        print(
            "-" * 54
        )

        for item in ready:

            print(
                f"{item.opportunity_id} | "
                f"{item.ticker} | "
                f"{item.direction.value} | "
                f"{item.broker_instrument_count} instruments"
            )

def opportunity_instruments(
    store: Stage3Store,
    opportunity_id: str,
    assessment_id: str,
) -> None:
    """
    Load one persisted TradeOpportunity, retrieve all Fineco
    instruments associated with its ticker and run the deterministic
    InstrumentSelector.

    This command does NOT create a TradeProposal and does NOT perform
    position sizing or portfolio-risk simulation.
    """

    opportunity = (
        store.get_trade_opportunity(
            opportunity_id
        )
    )

    if opportunity is None:

        print(
            "Trade opportunity not found: "
            f"{opportunity_id}"
        )

        return

    # ---------------------------------------------------------
    # Terminal states cannot enter instrument selection.
    # ---------------------------------------------------------

    if opportunity.status in {
        OpportunityStatus.REJECTED,
        OpportunityStatus.EXPIRED,
    }:

        print(
            "\nInstrument selection unavailable."
        )

        print(
            f"Opportunity status: "
            f"{opportunity.status.value}"
        )

        return

    # ---------------------------------------------------------
    # Portfolio lifecycle gate.
    # ---------------------------------------------------------

    lifecycle_gate = PortfolioLifecycleGate(
        store
    )

    gate_result = lifecycle_gate.evaluate(
        assessment_id
    )

    if (
        gate_result.opportunity_id
        != opportunity.opportunity_id
    ):
        raise ValueError(
            "PortfolioFitAssessment does not belong to the "
            "requested TradeOpportunity: "
            f"{assessment_id} -> {gate_result.opportunity_id}, "
            f"requested={opportunity.opportunity_id}"
        )

    print(
        "\n=== CIO PORTFOLIO LIFECYCLE GATE ===\n"
    )

    print(f"Assessment: {gate_result.assessment_id}")
    print(
        f"Portfolio decision: "
        f"{gate_result.portfolio_fit_decision.value}"
    )
    print(f"Lifecycle action: {gate_result.action.value}")
    print(f"Reason: {gate_result.reason_code.value}")

    if gate_result.warnings:
        print("\nLifecycle warnings")
        print("-" * 60)
        for warning in gate_result.warnings:
            print(f"- {warning}")

    if not gate_result.can_advance:
        print("\nInstrument selection blocked.")
        print(gate_result.reason)
        return

    if (
        gate_result.action
        == PortfolioLifecycleAction.ADVANCE_WITH_WARNING
    ):
        print(
            "\nInstrument selection allowed "
            "with portfolio warnings."
        )
    else:
        print("\nInstrument selection allowed.")

    # ---------------------------------------------------------
    # Refresh broker/cache state before selection.
    # ---------------------------------------------------------

    refreshed = (
        store.refresh_trade_opportunity_broker_state(
            opportunity_id
        )
    )

    if refreshed is None:

        print(
            "Trade opportunity not found: "
            f"{opportunity_id}"
        )

        return

    opportunity = refreshed

    # ---------------------------------------------------------
    # Fineco instruments still missing.
    # ---------------------------------------------------------

    if (
        opportunity.status
        == OpportunityStatus
        .WAITING_FOR_BROKER_INSTRUMENTS
    ):

        print(
            "\n=== CIO INSTRUMENT SELECTION ===\n"
        )

        print(
            f"Opportunity: "
            f"{opportunity.ticker} "
            f"{opportunity.direction.value} / "
            f"{opportunity.horizon.value}"
        )

        print(
            "\nStatus: "
            "WAITING_FOR_BROKER_INSTRUMENTS"
        )

        print(
            "\nACTION REQUIRED"
        )

        print(
            "-" * 60
        )

        print(
            "Provide Fineco screenshots for "
            f"{opportunity.ticker} and bulk-import "
            "the available instruments."
        )

        return

    # ---------------------------------------------------------
    # Retrieve complete Fineco family.
    # ---------------------------------------------------------

    instruments = (
        store.find_fineco_instruments(
            opportunity.ticker
        )
    )

    if not instruments:

        print(
            "No Fineco instruments found for "
            f"{opportunity.ticker}."
        )

        return

    selector = (
        InstrumentSelector()
    )

    candidates = selector.select(
        opportunity,
        instruments,
    )

    # ---------------------------------------------------------
    # Persist complete selector result.
    # ---------------------------------------------------------

    store.replace_instrument_candidates(
        opportunity.opportunity_id,
        candidates,
    )

    instrument_by_id = {
        instrument.instrument_id:
            instrument
        for instrument in instruments
    }

    # ---------------------------------------------------------
    # Output
    # ---------------------------------------------------------

    print(
        "\n=== CIO INSTRUMENT SELECTION ===\n"
    )

    print(
        f"Opportunity ID: "
        f"{opportunity.opportunity_id}"
    )

    print(
        f"Ticker:         "
        f"{opportunity.ticker}"
    )

    print(
        f"Direction:      "
        f"{opportunity.direction.value}"
    )

    print(
        f"Horizon:        "
        f"{opportunity.horizon.value}"
    )

    print(
        f"Confidence:     "
        f"{opportunity.confidence:.0%}"
    )

    print(
        f"Fineco records: "
        f"{len(instruments)}"
    )

    print()

    header = (
        f"{'Rank':>4}  "
        f"{'Eligible':<9}"
        f"{'Score':>7}  "
        f"{'Type':<12}"
        f"{'Mode':<16}"
        f"{'Relation':<16}"
        f"{'Market':<11}"
        f"{'BrokerLev':>10}  "
        f"{'Description'}"
    )

    print(
        header
    )

    print(
        "-" * len(header)
    )

    eligible_rank = 0

    for candidate in candidates:

        instrument = (
            instrument_by_id[
                candidate.instrument_id
            ]
        )

        if candidate.eligible:
            eligible_rank += 1
            rank = str(
                eligible_rank
            )
        else:
            rank = "-"

        score = (
            f"{candidate.suitability_score:.2f}"
            if candidate.suitability_score
            is not None
            else "?"
        )

        broker_leverage = (
            f"{instrument.broker_leverage:g}x"
            if instrument.broker_leverage
            is not None
            else "UNKNOWN"
        )

        description = (
            instrument.description
            or instrument.instrument_id
        )

        market = (
            instrument.market
            or "UNKNOWN"
        )

        print(
            f"{rank:>4}  "
            f"{_format_bool(candidate.eligible):<9}"
            f"{score:>7}  "
            f"{instrument.instrument_type.value:<12}"
            f"{instrument.trading_mode.value:<16}"
            f"{instrument.exposure_relationship.value:<16}"
            f"{market:<11}"
            f"{broker_leverage:>10}  "
            f"{description}"
        )

        if (
            not candidate.eligible
            and candidate.rejection_reason
        ):

            print(
                "      rejection: "
                f"{candidate.rejection_reason}"
            )

        print(
            "      instrument_id="
            f"{instrument.instrument_id}"
        )

    eligible = [
        item
        for item in candidates
        if item.eligible
    ]

    if eligible:

        opportunity = (
            store
            .mark_trade_opportunity_instruments_ranked(
                opportunity.opportunity_id
            )
        )

    print(
        "\nSelection summary"
    )

    print(
        "-" * 60
    )

    print(
        f"Persisted candidates: "
        f"{len(candidates)}"
    )

    if opportunity is not None:

        print(
            f"Opportunity status: "
            f"{opportunity.status.value}"
        )

    print(
        f"Eligible: "
        f"{len(eligible)} / "
        f"{len(candidates)}"
    )

    if eligible:

        best = (
            eligible[0]
        )

        best_instrument = (
            instrument_by_id[
                best.instrument_id
            ]
        )

        best_description = (
            best_instrument.description
            or best_instrument.instrument_id
        )

        print(
            "\nTop-ranked instrument candidate:"
        )

        print(
            f"  {best_description}"
        )

        print(
            f"  instrument_id="
            f"{best.instrument_id}"
        )

        print(
            f"  suitability_score="
            f"{best.suitability_score:.2f}"
        )

        print(
            "\nNOTE:"
        )

        print(
            "This is an Instrument Selector ranking, "
            "not yet a final Trade Proposal."
        )

        print(
            "Position sizing, costs and portfolio-risk "
            "simulation still have to be applied."
        )

    else:

        print(
            "\nNo eligible Fineco instrument can currently "
            "implement this opportunity."
        )


def opportunity_candidates(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    opportunity = (
        store.get_trade_opportunity(
            opportunity_id
        )
    )

    if opportunity is None:

        print(
            "Trade opportunity not found: "
            f"{opportunity_id}"
        )

        return

    candidates = (
        store.list_instrument_candidates(
            opportunity_id
        )
    )

    print(
        "\n=== PERSISTED INSTRUMENT CANDIDATES ===\n"
    )

    print(
        f"Opportunity: "
        f"{opportunity.ticker} "
        f"{opportunity.direction.value} / "
        f"{opportunity.horizon.value}"
    )

    print(
        f"Status: "
        f"{opportunity.status.value}\n"
    )

    if not candidates:

        print(
            "No persisted Instrument Selector results."
        )

        return

    header = (
        f"{'Rank':>4}  "
        f"{'Eligible':<9}"
        f"{'Score':>7}  "
        f"{'Instrument ID'}"
    )

    print(
        header
    )

    print(
        "-" * len(header)
    )

    eligible_rank = 0

    for candidate in candidates:

        if candidate.eligible:

            eligible_rank += 1

            rank = str(
                eligible_rank
            )

        else:

            rank = "-"

        score = (
            f"{candidate.suitability_score:.2f}"
            if candidate.suitability_score
            is not None
            else "?"
        )

        print(
            f"{rank:>4}  "
            f"{_format_bool(candidate.eligible):<9}"
            f"{score:>7}  "
            f"{candidate.instrument_id}"
        )

        if candidate.rejection_reason:

            print(
                "      rejection: "
                f"{candidate.rejection_reason}"
            )


def opportunity_size(
    store: Stage3Store,
    opportunity_id: str,
    instrument_id: str | None = None,
) -> None:

    opportunity = (
        store.get_trade_opportunity(
            opportunity_id
        )
    )

    if opportunity is None:

        print(
            "Trade opportunity not found: "
            f"{opportunity_id}"
        )

        return

    if opportunity.status not in {
        OpportunityStatus.INSTRUMENTS_RANKED,
        OpportunityStatus.POSITION_SIZED,
    }:

        print(
            "\nPosition sizing unavailable."
        )

        print(
            "Opportunity must first have "
            "persisted ranked instruments."
        )

        print(
            f"Current status: "
            f"{opportunity.status.value}"
        )

        return

    if instrument_id is None:

        candidate = (
            store.get_top_instrument_candidate(
                opportunity_id
            )
        )

        selection_mode = (
            "AUTO_TOP_RANKED"
        )

    else:

        candidates = (
            store.list_instrument_candidates(
                opportunity_id
            )
        )

        candidate = next(
            (
                item
                for item in candidates
                if (
                    item.instrument_id
                    == instrument_id
                )
            ),
            None,
        )

        selection_mode = (
            "OPERATOR_OVERRIDE"
        )

    if candidate is None:

        print(
            "No persisted instrument candidate found"
            + (
                "."
                if instrument_id is None
                else (
                    f": {instrument_id}"
                )
            )
        )

        return

    if not candidate.eligible:

        print(
            "\nInstrument override rejected."
        )

        print(
            f"Instrument ID: "
            f"{candidate.instrument_id}"
        )

        print(
            "Reason: candidate is not eligible "
            "for this opportunity."
        )

        if candidate.rejection_reason:

            print(
                f"Selector rejection: "
                f"{candidate.rejection_reason}"
            )

        return

    instrument = (
        store.get_fineco_instrument(
            candidate.instrument_id
        )
    )

    if instrument is None:

        print(
            "Fineco instrument not found: "
            f"{candidate.instrument_id}"
        )

        return

    account_state = (
        store.get_latest_account_state()
    )

    if account_state is None:

        print(
            "No CIO Account State configured."
        )

        return

    print(
        "\n=== CIO POSITION SIZING ===\n"
    )

    print(
        f"Opportunity:  "
        f"{opportunity.ticker} "
        f"{opportunity.direction.value}"
    )

    instrument_description = (
        instrument.description
        or instrument.instrument_id
    )

    print(
        f"Instrument:   "
        f"{instrument_description}"
    )

    print(
        f"Instrument ID: "
        f"{instrument.instrument_id}"
    )

    selector_score = (
        f"{candidate.suitability_score:.2f}"
        if candidate.suitability_score
        is not None
        else "UNKNOWN"
    )

    print(
        f"Selector score: "
        f"{selector_score}"
    )

    print(
        f"Selection mode: "
        f"{selection_mode}"
    )

    # ---------------------------------------------------------
    # Reference price
    # ---------------------------------------------------------

    reference_default = None

    if instrument.market_snapshot:

        snapshot = instrument.market_snapshot

        if (
            snapshot.bid is not None
            and snapshot.ask is not None
        ):

            reference_default = (
                snapshot.bid
                + snapshot.ask
            ) / 2.0

        elif snapshot.ask is not None:

            reference_default = (
                snapshot.ask
            )

        elif snapshot.bid is not None:

            reference_default = (
                snapshot.bid
            )

    reference_price = (
        _prompt_float_default(
            "Reference price",
            reference_default,
            minimum=0.00000001,
        )
    )

    if reference_price is None:

        raise ValueError(
            "Reference price is required"
        )

    # ---------------------------------------------------------
    # FX
    # ---------------------------------------------------------

    if (
        instrument.quote_currency
        == Currency.EUR
    ):

        fx_to_eur = 1.0

        print(
            "FX to EUR: 1.0 "
            "(EUR instrument)"
        )

    else:

        fx_to_eur = (
            _prompt_float(
                f"1 {instrument.quote_currency.value} "
                "= how many EUR?: ",
                minimum=0.00000001,
            )
        )

        assert fx_to_eur is not None

    # ---------------------------------------------------------
    # Stop / exposure override
    # ---------------------------------------------------------

    stop_price = (
        _prompt_float(
            "Stop price [optional]: ",
            optional=True,
            minimum=0.00000001,
        )
    )

    requested_exposure = (
        _prompt_float(
            "Override target exposure EUR "
            "[optional]: €",
            optional=True,
            minimum=0.000001,
        )
    )

    sizing = (
        PositionSizer().size(
            opportunity=opportunity,
            instrument=instrument,
            account_state=account_state,
            reference_price=reference_price,
            fx_to_eur=fx_to_eur,
            stop_price=stop_price,
            requested_exposure_eur=(
                requested_exposure
            ),
        )
    )

    store.save_position_sizing(
        sizing
    )

    if sizing.constraints_passed:

        updated = (
            store
            .mark_trade_opportunity_position_sized(
                opportunity_id,
                sizing.sizing_id,
            )
        )

    else:

        updated = opportunity

    print(
        "\n=== POSITION SIZING RESULT ===\n"
    )

    print(
        f"Sizing ID:       "
        f"{sizing.sizing_id}"
    )

    print(
        f"Execution side:  "
        f"{sizing.execution_side.value}"
    )

    print(
        f"Quantity:        "
        f"{sizing.quantity:g}"
    )

    print(
        f"Reference price: "
        f"{sizing.reference_price:g} "
        f"{sizing.currency.value}"
    )

    if sizing.stop_price is not None:

        print(
            f"Stop price:      "
            f"{sizing.stop_price:g}"
        )

    print(
        f"Gross exposure:  "
        f"€{sizing.gross_exposure_eur:,.2f}"
    )

    print(
        f"Capital required: "
        f"€{sizing.estimated_capital_required_eur:,.2f}"
    )

    if (
        sizing.estimated_margin_eur
        is not None
    ):

        print(
            f"Estimated margin: "
            f"€{sizing.estimated_margin_eur:,.2f}"
        )

    if (
        sizing.estimated_max_loss_eur
        is not None
    ):

        print(
            f"Estimated max loss: "
            f"€{sizing.estimated_max_loss_eur:,.2f}"
        )

    if sizing.risk_budget_eur is not None:

        print(
            f"Risk budget:      "
            f"€{sizing.risk_budget_eur:,.2f}"
        )

    print(
        f"Constraints:      "
        f"{'PASS' if sizing.constraints_passed else 'FAIL'}"
    )

    if sizing.violated_constraints:

        print(
            "\nViolations"
        )

        print(
            "-" * 60
        )

        for violation in (
            sizing.violated_constraints
        ):

            print(
                f"- {violation}"
            )

    if updated is not None:

        print(
            f"\nOpportunity status: "
            f"{updated.status.value}"
        )



def _print_trade_proposal(
    proposal: TradeProposal,
) -> None:

    print(
        "\n=== CIO PRELIMINARY TRADE PROPOSAL ===\n"
    )

    print(
        f"Proposal ID:    "
        f"{proposal.proposal_id}"
    )
    print(
        f"Opportunity ID: "
        f"{proposal.opportunity_id}"
    )
    print(
        f"Snapshot:       "
        f"{proposal.snapshot_id}"
    )
    print(
        f"Ticker:         "
        f"{proposal.ticker}"
    )
    print(
        f"Direction:      "
        f"{proposal.direction.value}"
    )

    if proposal.execution_side is not None:
        print(
            f"Execution side: "
            f"{proposal.execution_side.value}"
        )

    print(
        f"Instrument ID:  "
        f"{proposal.instrument_id}"
    )

    if proposal.sizing_id is not None:
        print(
            f"Sizing ID:      "
            f"{proposal.sizing_id}"
        )

    print(
        f"Quantity:       "
        f"{proposal.quantity:g}"
    )
    print(
        f"Reference:      "
        f"{proposal.reference_price:g} "
        f"{proposal.currency.value}"
    )
    print(
        f"Entry type:     "
        f"{proposal.entry_type}"
    )

    if proposal.entry_price is not None:
        print(
            f"Entry price:    "
            f"{proposal.entry_price:g}"
        )

    if proposal.stop_price is not None:
        print(
            f"Stop price:     "
            f"{proposal.stop_price:g}"
        )

    if proposal.target_1 is not None:
        print(
            f"Target 1:       "
            f"{proposal.target_1:g}"
        )

    if proposal.target_2 is not None:
        print(
            f"Target 2:       "
            f"{proposal.target_2:g}"
        )

    print(
        f"Gross exposure: "
        f"€{proposal.gross_exposure_eur:,.2f}"
    )

    if proposal.estimated_margin_eur is not None:
        print(
            f"Est. margin:    "
            f"€{proposal.estimated_margin_eur:,.2f}"
        )

    if proposal.estimated_max_loss_eur is not None:
        print(
            f"Est. max loss:  "
            f"€{proposal.estimated_max_loss_eur:,.2f}"
        )

    if (
        proposal.expected_holding_min_days is not None
        or proposal.expected_holding_max_days is not None
    ):
        minimum = (
            str(proposal.expected_holding_min_days)
            if proposal.expected_holding_min_days is not None
            else "?"
        )
        maximum = (
            str(proposal.expected_holding_max_days)
            if proposal.expected_holding_max_days is not None
            else "?"
        )
        print(
            f"Holding:        "
            f"{minimum}-{maximum} days"
        )

    print(
        f"Status:         "
        f"{proposal.status.value}"
    )

    print(
        "\nNOTE:"
    )
    print(
        "This is a preliminary CIO TradeProposal."
    )
    print(
        "Portfolio simulation, CIO decision and manual "
        "broker execution are still pending."
    )


def opportunity_propose(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    service = TradeProposalService(
        store
    )

    print(
        "\n=== CREATE PRELIMINARY TRADE PROPOSAL ===\n"
    )

    entry_type = (
        input(
            "Entry type [MARKET]: "
        )
        .strip()
        .upper()
        or "MARKET"
    )

    entry_price = _prompt_float(
        "Entry price [optional]: ",
        optional=True,
        minimum=0.00000001,
    )

    target_1 = _prompt_float(
        "Target 1 [optional]: ",
        optional=True,
        minimum=0.00000001,
    )

    target_2 = _prompt_float(
        "Target 2 [optional]: ",
        optional=True,
        minimum=0.00000001,
    )

    proposal = service.create_preliminary_proposal(
        opportunity_id,
        entry_type=entry_type,
        entry_price=entry_price,
        target_1=target_1,
        target_2=target_2,
    )

    print(
        "\nTradeProposal created and persisted."
    )

    _print_trade_proposal(
        proposal
    )

    opportunity = store.get_trade_opportunity(
        opportunity_id
    )

    if opportunity is not None:
        print(
            f"\nOpportunity status: "
            f"{opportunity.status.value}"
        )


def opportunity_proposal(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    proposal = store.get_latest_trade_proposal(
        opportunity_id
    )

    if proposal is None:
        print(
            "No persisted TradeProposal found for "
            f"{opportunity_id}."
        )
        return

    _print_trade_proposal(
        proposal
    )



def _print_portfolio_simulation(
    simulation: PortfolioSimulation,
) -> None:

    before = simulation.before
    after = simulation.after
    delta = simulation.delta

    print(
        "\n=== CIO PORTFOLIO SIMULATION ===\n"
    )

    print(
        f"Simulation ID: "
        f"{simulation.simulation_id}"
    )

    print(
        f"Proposal ID:   "
        f"{simulation.proposal_id}"
    )

    print(
        f"Snapshot ID:   "
        f"{simulation.snapshot_id}"
    )

    print(
        "\nExposure BEFORE -> AFTER"
    )

    print(
        "-" * 68
    )

    print(
        f"{'Metric':<24}"
        f"{'Before':>14}"
        f"{'After':>14}"
        f"{'Delta':>16}"
    )

    print(
        "-" * 68
    )

    rows = [
        (
            "Gross exposure",
            before.gross_exposure_eur,
            after.gross_exposure_eur,
            delta.gross_exposure_eur,
        ),
        (
            "Net exposure",
            before.net_exposure_eur,
            after.net_exposure_eur,
            delta.net_exposure_eur,
        ),
        (
            "LONG exposure",
            before.long_exposure_eur,
            after.long_exposure_eur,
            delta.long_exposure_eur,
        ),
        (
            "SHORT exposure",
            before.short_exposure_eur,
            after.short_exposure_eur,
            delta.short_exposure_eur,
        ),
    ]

    for (
        label,
        before_value,
        after_value,
        delta_value,
    ) in rows:

        print(
            f"{label:<24}"
            f"€{before_value:>13,.2f}"
            f"€{after_value:>13,.2f}"
            f"{delta_value:>+16,.2f}"
        )

    print(
        "\nQuantitative risk"
    )

    print(
        "-" * 68
    )

    print(
        "Portfolio volatility: "
        f"{before.portfolio_volatility_pct:.2f}%"
        " -> "
        f"{after.portfolio_volatility_pct:.2f}% "
        f"({delta.portfolio_volatility_pct:+.2f} pp)"
    )

    print(
        "Portfolio beta:       "
        f"{before.portfolio_beta:.3f}"
        " -> "
        f"{after.portfolio_beta:.3f} "
        f"({delta.portfolio_beta:+.3f})"
    )

    print(
        "VaR 95% 1D:           "
        f"€{before.var_95_1d_eur:,.2f}"
        " -> "
        f"€{after.var_95_1d_eur:,.2f} "
        f"({delta.var_95_1d_eur:+,.2f})"
    )

    print(
        "CVaR 95% 1D:          "
        f"€{before.cvar_95_1d_eur:,.2f}"
        " -> "
        f"€{after.cvar_95_1d_eur:,.2f} "
        f"({delta.cvar_95_1d_eur:+,.2f})"
    )

    print(
        "Top 5 concentration:  "
        f"{before.top5_concentration_pct:.2f}%"
        " -> "
        f"{after.top5_concentration_pct:.2f}% "
        f"({delta.top5_concentration_pct:+.2f} pp)"
    )

    print(
        "Effective positions:  "
        f"{before.effective_positions:.2f}"
        " -> "
        f"{after.effective_positions:.2f} "
        f"({delta.effective_positions:+.2f})"
    )

    if (
        before.analytical_coverage_pct
        is not None
    ):

        print(
            "Analytical coverage: "
            f"{before.analytical_coverage_pct:.2f}%"
        )

    print(
        "\nProjected cash"
    )

    print(
        "-" * 68
    )

    print(
        "EUR: "
        + (
            f"€{simulation.cash_after_eur:,.2f}"
            if simulation.cash_after_eur is not None
            else "UNKNOWN"
        )
    )

    print(
        "USD: "
        + (
            f"${simulation.cash_after_usd:,.2f}"
            if simulation.cash_after_usd is not None
            else "UNKNOWN"
        )
    )

    print(
        "\nConstraints: "
        f"{'PASS' if simulation.constraints_passed else 'FAIL'}"
    )

    if simulation.violated_constraints:

        print(
            "\nViolations"
        )

        print(
            "-" * 68
        )

        for violation in (
            simulation.violated_constraints
        ):

            print(
                f"- {violation}"
            )

    if simulation.warnings:

        print(
            "\nWarnings"
        )

        print(
            "-" * 68
        )

        for warning in (
            simulation.warnings
        ):

            print(
                f"- {warning}"
            )

    print(
        "\nNOTE:"
    )

    print(
        "This is a hypothetical portfolio simulation. "
        "No broker order has been executed."
    )


def opportunity_simulate(
    store: Stage3Store,
    opportunity_id: str,
) -> None:
    """
    Create and persist a PortfolioSimulation for the latest
    TradeProposal belonging to one opportunity.

    PortfolioSnapshot, PortfolioRiskState BEFORE and AccountState
    are resolved automatically by PortfolioSimulationService from
    the persisted Stage 3 state.

    No broker order is executed.
    """

    proposal = (
        store.get_latest_trade_proposal(
            opportunity_id
        )
    )

    if proposal is None:

        print(
            "No persisted TradeProposal found for "
            f"{opportunity_id}."
        )

        return

    snapshot = (
        store.get_portfolio_snapshot(
            proposal.snapshot_id
        )
    )

    if snapshot is None:

        print(
            "\nPortfolio simulation unavailable."
        )

        print(
            "The TradeProposal references a PortfolioSnapshot "
            "that is not persisted in Stage3Store:"
        )

        print(
            f"  {proposal.snapshot_id}"
        )

        print(
            "\nThis is expected for old opportunities created "
            "with SNAP-MANUAL-* during early Stage 3 testing."
        )

        print(
            "A real PortfolioSnapshot must be persisted before "
            "this opportunity can be simulated."
        )

        return

    service = (
        build_canonical_portfolio_simulation_service(
            store
        )
    )

    try:

        simulation = (
            service.create_simulation(
                opportunity_id
            )
        )

    except ValueError as exc:

        print(
            "\nPortfolio simulation unavailable."
        )

        print(
            str(exc)
        )

        return

    print(
        "\nPortfolioSimulation created and persisted."
    )

    _print_portfolio_simulation(
        simulation
    )


def opportunity_simulation(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    proposal = (
        store.get_latest_trade_proposal(
            opportunity_id
        )
    )

    if proposal is None:

        print(
            "No persisted TradeProposal found for "
            f"{opportunity_id}."
        )

        return

    simulation = (
        store.get_latest_portfolio_simulation(
            proposal.proposal_id
        )
    )

    if simulation is None:

        print(
            "No persisted PortfolioSimulation found for "
            f"{opportunity_id}."
        )

        return

    _print_portfolio_simulation(
        simulation
    )



# =============================================================
# CIO Decision
# =============================================================


def _print_cio_decision(
    decision: CioDecision,
) -> None:

    print(
        "\n=== CIO DECISION ===\n"
    )

    print(
        f"Decision ID:    "
        f"{decision.decision_id}"
    )

    print(
        f"Opportunity ID: "
        f"{decision.opportunity_id}"
    )

    print(
        f"Proposal ID:    "
        f"{decision.proposal_id}"
    )

    print(
        f"Simulation ID:  "
        f"{decision.simulation_id}"
    )

    print(
        f"Snapshot ID:    "
        f"{decision.snapshot_id}"
    )

    print(
        f"Decision:       "
        f"{decision.decision.value}"
    )

    print(
        f"Confidence:     "
        f"{decision.confidence:.0%}"
    )

    print(
        f"Status:         "
        f"{decision.status.value}"
    )

    print(
        f"Created at:     "
        f"{decision.created_at.isoformat()}"
    )

    print(
        "\nRationale"
    )

    print(
        "-" * 68
    )

    print(
        decision.rationale
    )

    if decision.assessments:

        print(
            "\nEvidence assessments"
        )

        print(
            "-" * 68
        )

        for assessment in (
            decision.assessments
        ):

            critical = (
                "CRITICAL"
                if assessment.critical
                else "NON-CRITICAL"
            )

            print(
                f"- [{assessment.evidence_type.value}] "
                f"{assessment.code}: "
                f"{assessment.outcome.value} "
                f"({critical})"
            )

            print(
                f"  {assessment.summary}"
            )

    if decision.required_changes:

        print(
            "\nRequired changes"
        )

        print(
            "-" * 68
        )

        for change in (
            decision.required_changes
        ):

            mandatory = (
                "MANDATORY"
                if change.mandatory
                else "OPTIONAL"
            )

            print(
                f"- {change.change_type.value} "
                f"({mandatory})"
            )

            print(
                f"  {change.description}"
            )

    if decision.warnings:

        print(
            "\nWarnings"
        )

        print(
            "-" * 68
        )

        for warning in (
            decision.warnings
        ):

            print(
                f"- {warning}"
            )

    print(
        "\nGovernance"
    )

    print(
        "-" * 68
    )

    print(
        "Hard constraints passed: "
        f"{_format_bool(decision.hard_constraints_passed)}"
    )

    print(
        "Critical evidence complete: "
        f"{_format_bool(decision.critical_evidence_complete)}"
    )

    print(
        "\nNOTE:"
    )

    print(
        "This is a CIO decision on the persisted TradeProposal "
        "and PortfolioSimulation."
    )

    print(
        "No broker order has been executed. "
        "Execution remains manual."
    )


def opportunity_decide(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    service = CioDecisionService(store)

    try:
        decision = service.create_decision(
            opportunity_id
        )
    except ValueError as exc:
        print("\nCIO decision unavailable.")
        print(str(exc))
        return

    print("\nCioDecision created and persisted.")
    _print_cio_decision(decision)


def opportunity_decision(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    decision = store.get_latest_cio_decision(
        opportunity_id
    )

    if decision is None:
        print(
            "No persisted CioDecision found for "
            f"{opportunity_id}."
        )
        return

    _print_cio_decision(decision)



# =============================================================
# Execution Plan
# =============================================================


def _print_execution_plan(
    plan: ExecutionPlan,
) -> None:

    print(
        "\n=== CIO EXECUTION PLAN ===\n"
    )

    print(f"Execution Plan ID: {plan.execution_plan_id}")
    print(f"Opportunity ID:    {plan.opportunity_id}")
    print(f"Proposal ID:       {plan.proposal_id}")
    print(f"Simulation ID:     {plan.simulation_id}")
    print(f"Decision ID:       {plan.decision_id}")
    print(f"Snapshot ID:       {plan.snapshot_id}")
    print(f"Created at:        {plan.created_at.isoformat()}")

    print(
        "\nBroker instruction"
    )
    print("-" * 68)

    print(f"Broker:            {plan.broker}")
    print(f"Underlying:        {plan.underlying}")
    print(f"Instrument ID:     {plan.instrument_id}")
    print(
        "Description:       "
        f"{_format_optional(plan.instrument_description)}"
    )
    print(
        "Broker symbol:     "
        f"{_format_optional(plan.broker_symbol)}"
    )
    print(
        "Market:            "
        f"{_format_optional(plan.market)}"
    )
    print(f"Currency:          {plan.currency.value}")
    print(f"Direction:         {plan.direction.value}")
    print(f"Execution side:    {plan.execution_side.value}")
    print(f"Quantity:          {plan.quantity:g}")
    print(f"Order type:        {plan.order_type}")

    if plan.reference_price is not None:
        print(
            f"Reference price:   {plan.reference_price:g} "
            f"{plan.currency.value}"
        )

    if plan.entry_price is not None:
        print(
            f"Entry price:       {plan.entry_price:g} "
            f"{plan.currency.value}"
        )

    if plan.stop_price is not None:
        print(
            f"Stop price:        {plan.stop_price:g} "
            f"{plan.currency.value}"
        )

    if plan.target_1 is not None:
        print(
            f"Target 1:          {plan.target_1:g} "
            f"{plan.currency.value}"
        )

    if plan.target_2 is not None:
        print(
            f"Target 2:          {plan.target_2:g} "
            f"{plan.currency.value}"
        )

    if plan.fx_to_eur is not None:
        print(
            f"FX to EUR:         {plan.fx_to_eur:g}"
        )

    print(
        "\nRisk / capital"
    )
    print("-" * 68)

    print(
        f"Gross exposure:    "
        f"€{plan.gross_exposure_eur:,.2f}"
    )

    if plan.estimated_capital_required_eur is not None:
        print(
            f"Capital required:  "
            f"€{plan.estimated_capital_required_eur:,.2f}"
        )

    if plan.estimated_max_loss_eur is not None:
        print(
            f"Est. max loss:     "
            f"€{plan.estimated_max_loss_eur:,.2f}"
        )

    if (
        plan.expected_holding_min_days is not None
        or plan.expected_holding_max_days is not None
    ):
        minimum = (
            str(plan.expected_holding_min_days)
            if plan.expected_holding_min_days is not None
            else "?"
        )
        maximum = (
            str(plan.expected_holding_max_days)
            if plan.expected_holding_max_days is not None
            else "?"
        )
        print(
            f"Expected holding:  "
            f"{minimum}-{maximum} days"
        )

    print(
        "\nGovernance"
    )
    print("-" * 68)
    print(f"Status:            {plan.status.value}")

    if plan.execution_notes:
        print(
            f"Execution notes:   "
            f"{plan.execution_notes}"
        )

    print(
        "\nNOTE:"
    )
    print(
        "This is a broker-ready manual execution instruction."
    )
    print(
        "No broker order has been executed by the CIO."
    )
    print(
        "Execution must remain manual and requires explicit "
        "operator confirmation."
    )


def opportunity_execution_plan(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    service = ExecutionPlanService(
        store
    )

    try:
        plan = service.create_execution_plan(
            opportunity_id
        )
    except ValueError as exc:
        print(
            "\nExecution plan unavailable."
        )
        print(str(exc))
        return

    print(
        "\nExecutionPlan created/resolved and persisted."
    )

    _print_execution_plan(
        plan
    )


def opportunity_execution(
    store: Stage3Store,
    opportunity_id: str,
) -> None:

    plan = store.get_latest_execution_plan(
        opportunity_id
    )

    if plan is None:
        print(
            "No persisted ExecutionPlan found for "
            f"{opportunity_id}."
        )
        return

    _print_execution_plan(
        plan
    )



# =============================================================
# Operator Confirmation
# =============================================================


def _print_operator_confirmation(
    confirmation: OperatorConfirmation,
) -> None:

    print(
        "\n=== CIO OPERATOR CONFIRMATION ===\n"
    )

    print(
        f"Confirmation ID:  "
        f"{confirmation.confirmation_id}"
    )

    print(
        f"Execution Plan:   "
        f"{confirmation.execution_plan_id}"
    )

    print(
        f"Created at:       "
        f"{confirmation.created_at.isoformat()}"
    )

    print(
        f"Outcome:          "
        f"{confirmation.outcome.value}"
    )

    print(
        f"Source:           "
        f"{confirmation.source.value}"
    )

    if confirmation.executed_quantity is not None:
        print(
            f"Executed quantity:"
            f" {confirmation.executed_quantity:g}"
        )

    if confirmation.executed_price is not None:
        print(
            f"Executed price:   "
            f"{confirmation.executed_price:g}"
        )

    if confirmation.commission_eur is not None:
        print(
            f"Commission EUR:   "
            f"€{confirmation.commission_eur:,.2f}"
        )

    if confirmation.broker_order_reference:
        print(
            f"Broker reference: "
            f"{confirmation.broker_order_reference}"
        )

    if confirmation.notes:
        print(
            f"Notes:            "
            f"{confirmation.notes}"
        )

    print(
        "\nNOTE:"
    )

    print(
        "This is an operator-reported audit record."
    )

    print(
        "The CIO did not execute the broker order automatically."
    )


def opportunity_confirm(
    store: Stage3Store,
    opportunity_id: str,
) -> None:
    """
    Explicitly confirm the outcome of the latest persisted ExecutionPlan
    for one opportunity.

    The operator must first perform (or cancel) the broker action
    manually. This command only records the reported outcome.
    """

    plan = store.get_latest_execution_plan(
        opportunity_id
    )

    if plan is None:

        print(
            "No persisted ExecutionPlan found for "
            f"{opportunity_id}."
        )

        return

    print(
        "\n=== OPERATOR CONFIRMATION ===\n"
    )

    print(
        f"Execution Plan ID: "
        f"{plan.execution_plan_id}"
    )

    print(
        f"Underlying:        "
        f"{plan.underlying}"
    )

    print(
        f"Instrument ID:     "
        f"{plan.instrument_id}"
    )

    print(
        f"Execution side:    "
        f"{plan.execution_side.value}"
    )

    print(
        f"Authorized qty:    "
        f"{plan.quantity:g}"
    )

    print(
        f"Current status:    "
        f"{plan.status.value}"
    )

    print(
        "\nIMPORTANT:"
    )

    print(
        "Use this command only after you have manually executed "
        "or cancelled the broker instruction."
    )

    outcome_raw = _prompt_choice(
        "Outcome",
        [
            item.value
            for item
            in OperatorConfirmationOutcome
        ],
    )

    outcome = OperatorConfirmationOutcome(
        outcome_raw
    )

    service = OperatorConfirmationService(
        store
    )

    try:

        if (
            outcome
            == OperatorConfirmationOutcome.EXECUTED
        ):

            executed_quantity = _prompt_float(
                "Actual executed quantity: ",
                minimum=0.00000001,
            )

            assert executed_quantity is not None

            executed_price = _prompt_float(
                f"Actual executed price "
                f"[{plan.currency.value}]: ",
                minimum=0.00000001,
            )

            assert executed_price is not None

            commission_eur = _prompt_float(
                "Commission EUR [optional]: €",
                optional=True,
                minimum=0,
            )

            broker_order_reference = (
                input(
                    "Broker order/reference "
                    "[optional]: "
                )
                .strip()
                or None
            )

            notes = (
                input(
                    "Operator notes [optional]: "
                )
                .strip()
                or None
            )

            confirmation = (
                service.confirm_executed(
                    plan.execution_plan_id,
                    executed_quantity=executed_quantity,
                    executed_price=executed_price,
                    commission_eur=commission_eur,
                    broker_order_reference=(
                        broker_order_reference
                    ),
                    notes=notes,
                )
            )

        else:

            notes = (
                input(
                    "Cancellation notes "
                    "[optional]: "
                )
                .strip()
                or None
            )

            confirmation = (
                service.confirm_cancelled(
                    plan.execution_plan_id,
                    notes=notes,
                )
            )

    except ValueError as exc:

        print(
            "\nOperator confirmation unavailable."
        )

        print(
            str(exc)
        )

        return

    print(
        "\nOperatorConfirmation created and persisted."
    )

    _print_operator_confirmation(
        confirmation
    )

    updated_plan = store.get_execution_plan(
        plan.execution_plan_id
    )

    if updated_plan is not None:

        print(
            "\nExecutionPlan lifecycle"
        )

        print(
            "-" * 68
        )

        print(
            f"Previous status: "
            f"{plan.status.value}"
        )

        print(
            f"Current status:  "
            f"{updated_plan.status.value}"
        )


def opportunity_confirmation(
    store: Stage3Store,
    opportunity_id: str,
) -> None:
    """
    Show the latest persisted OperatorConfirmation for the latest
    ExecutionPlan belonging to one opportunity.
    """

    plan = store.get_latest_execution_plan(
        opportunity_id
    )

    if plan is None:

        print(
            "No persisted ExecutionPlan found for "
            f"{opportunity_id}."
        )

        return

    confirmation = (
        store
        .get_latest_operator_confirmation_for_execution_plan(
            plan.execution_plan_id
        )
    )

    if confirmation is None:

        print(
            "No persisted OperatorConfirmation found for "
            f"{plan.execution_plan_id}."
        )

        return

    _print_operator_confirmation(
        confirmation
    )



# =============================================================
# Trade Outcome
# =============================================================


def _print_trade_outcome(
    outcome: TradeOutcome,
) -> None:

    print(
        "\n=== CIO TRADE OUTCOME ===\n"
    )

    print(
        f"Outcome ID:       "
        f"{outcome.outcome_id}"
    )

    print(
        f"Opportunity ID:   "
        f"{outcome.opportunity_id}"
    )

    print(
        f"Execution Plan:   "
        f"{outcome.execution_plan_id}"
    )

    print(
        f"Confirmation ID:  "
        f"{outcome.confirmation_id}"
    )

    print(
        f"Ticker:           "
        f"{outcome.ticker}"
    )

    print(
        f"Direction:        "
        f"{outcome.direction.value}"
    )

    print(
        f"Status:           "
        f"{outcome.status.value}"
    )

    print(
        "\nEntry"
    )

    print(
        "-" * 68
    )

    print(
        f"Datetime:         "
        f"{outcome.entry_datetime.isoformat()}"
    )

    print(
        f"Quantity:         "
        f"{outcome.entry_quantity:g}"
    )

    print(
        f"Price:            "
        f"{outcome.entry_price:g} "
        f"{outcome.currency.value}"
    )

    print(
        "Entry FX to EUR:  "
        + (
            f"{outcome.entry_fx_to_eur:g}"
            if outcome.entry_fx_to_eur is not None
            else "N/A"
        )
    )

    print(
        "Entry commission: "
        + (
            f"€{outcome.entry_commission_eur:,.2f}"
            if outcome.entry_commission_eur is not None
            else "UNKNOWN"
        )
    )

    print(
        "\nApproved plan snapshot"
    )

    print(
        "-" * 68
    )

    print(
        "Reference price:  "
        + (
            f"{outcome.planned_reference_price:g} "
            f"{outcome.currency.value}"
            if outcome.planned_reference_price is not None
            else "UNKNOWN"
        )
    )

    print(
        "Stop:             "
        + (
            f"{outcome.planned_stop_price:g} "
            f"{outcome.currency.value}"
            if outcome.planned_stop_price is not None
            else "UNKNOWN"
        )
    )

    print(
        "Target 1:         "
        + (
            f"{outcome.planned_target_1:g} "
            f"{outcome.currency.value}"
            if outcome.planned_target_1 is not None
            else "UNKNOWN"
        )
    )

    print(
        "Target 2:         "
        + (
            f"{outcome.planned_target_2:g} "
            f"{outcome.currency.value}"
            if outcome.planned_target_2 is not None
            else "UNKNOWN"
        )
    )

    if outcome.status == TradeOutcomeStatus.CLOSED:

        print(
            "\nRealized exit"
        )

        print(
            "-" * 68
        )

        print(
            f"Datetime:         "
            f"{outcome.exit_datetime.isoformat()}"
        )

        print(
            f"Quantity:         "
            f"{outcome.exit_quantity:g}"
        )

        print(
            f"Price:            "
            f"{outcome.exit_price:g} "
            f"{outcome.currency.value}"
        )

        print(
            "Exit FX to EUR:   "
            + (
                f"{outcome.exit_fx_to_eur:g}"
                if outcome.exit_fx_to_eur is not None
                else "N/A"
            )
        )

        print(
            "Exit commission:  "
            + (
                f"€{outcome.exit_commission_eur:,.2f}"
                if outcome.exit_commission_eur is not None
                else "UNKNOWN"
            )
        )

        print(
            f"Exit reason:      "
            f"{outcome.exit_reason.value}"
        )

        print(
            "\nRealized economics"
        )

        print(
            "-" * 68
        )

        print(
            "Realized P/L:     "
            + (
                f"€{outcome.realized_pnl_eur:,.2f}"
                if outcome.realized_pnl_eur is not None
                else "UNKNOWN"
            )
        )

        print(
            "Realized return:  "
            + (
                f"{outcome.realized_return_pct:.2f}%"
                if outcome.realized_return_pct is not None
                else "UNKNOWN"
            )
        )

        print(
            "Holding period:   "
            + (
                f"{outcome.holding_days:.3f} days"
                if outcome.holding_days is not None
                else "UNKNOWN"
            )
        )

        print(
            "Entry slippage:   "
            + (
                f"{outcome.entry_slippage_pct:+.3f}%"
                if outcome.entry_slippage_pct is not None
                else "UNKNOWN"
            )
        )

    if outcome.notes:

        print(
            f"\nNotes: {outcome.notes}"
        )


def opportunity_outcome(
    store: Stage3Store,
    opportunity_id: str,
) -> None:
    """
    Create or idempotently resolve the OPEN TradeOutcome for the latest
    operator-confirmed execution.
    """

    notes = (
        input(
            "Outcome notes [optional]: "
        ).strip()
        or None
    )

    service = TradeOutcomeService(
        store
    )

    try:

        outcome = service.create_open_outcome(
            opportunity_id,
            notes=notes,
        )

    except ValueError as exc:

        print(
            "\nTradeOutcome unavailable."
        )

        print(
            str(exc)
        )

        return

    print(
        "\nTradeOutcome created/resolved."
    )

    _print_trade_outcome(
        outcome
    )


def opportunity_outcome_show(
    store: Stage3Store,
    opportunity_id: str,
) -> None:
    """
    Show the latest persisted TradeOutcome for one opportunity.
    """

    outcome = store.get_latest_trade_outcome(
        opportunity_id
    )

    if outcome is None:

        print(
            "No persisted TradeOutcome found for "
            f"{opportunity_id}."
        )

        return

    _print_trade_outcome(
        outcome
    )


def _prompt_exit_datetime() -> datetime:
    """
    ENTER uses current UTC time.

    Otherwise accepts ISO-8601. A timezone-naive value is interpreted as
    UTC because Stage 3 canonical timestamps are persisted in UTC.
    """

    while True:

        raw = input(
            "Exit datetime "
            "[ISO-8601, ENTER=current UTC]: "
        ).strip()

        if raw == "":

            return datetime.now(
                timezone.utc
            )

        try:

            value = datetime.fromisoformat(
                raw.replace(
                    "Z",
                    "+00:00",
                )
            )

        except ValueError:

            print(
                "Please enter a valid ISO-8601 datetime."
            )

            continue

        if value.tzinfo is None:

            value = value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(
            timezone.utc
        )


def opportunity_outcome_close(
    store: Stage3Store,
    opportunity_id: str,
) -> None:
    """
    Fully close the latest OPEN TradeOutcome using operator-supplied broker
    exit facts. V1 supports full close only.
    """

    outcome = store.get_latest_trade_outcome(
        opportunity_id
    )

    if outcome is None:

        print(
            "No persisted TradeOutcome found for "
            f"{opportunity_id}."
        )

        return

    if outcome.status != TradeOutcomeStatus.OPEN:

        print(
            "TradeOutcome close unavailable."
        )

        print(
            f"Latest outcome status is "
            f"{outcome.status.value}; expected OPEN."
        )

        return

    print(
        "\n=== CLOSE CIO TRADE OUTCOME ===\n"
    )

    print(
        f"Outcome ID:       "
        f"{outcome.outcome_id}"
    )

    print(
        f"Ticker:           "
        f"{outcome.ticker}"
    )

    print(
        f"Direction:        "
        f"{outcome.direction.value}"
    )

    print(
        f"Open quantity:    "
        f"{outcome.entry_quantity:g}"
    )

    print(
        f"Entry price:      "
        f"{outcome.entry_price:g} "
        f"{outcome.currency.value}"
    )

    exit_datetime = (
        _prompt_exit_datetime()
    )

    exit_price = _prompt_float(
        f"Actual exit price "
        f"[{outcome.currency.value}]: ",
        minimum=0.00000001,
    )

    assert exit_price is not None

    # Full-close V1: show quantity and require explicit confirmation by
    # pressing ENTER or re-entering the same quantity.
    exit_quantity = _prompt_float_default(
        "Exit quantity (full close)",
        outcome.entry_quantity,
        minimum=0.00000001,
    )

    assert exit_quantity is not None

    exit_commission_eur = _prompt_float(
        "Exit commission EUR "
        "[ENTER=0]: €",
        optional=True,
        minimum=0,
    )

    if exit_commission_eur is None:
        exit_commission_eur = 0.0

    exit_fx_to_eur = None

    if outcome.currency != Currency.EUR:

        exit_fx_to_eur = _prompt_float(
            f"1 {outcome.currency.value} = "
            "how many EUR?: ",
            minimum=0.00000001,
        )

        assert exit_fx_to_eur is not None

    exit_reason = TradeExitReason(
        _prompt_choice(
            "Exit reason",
            [
                item.value
                for item
                in TradeExitReason
            ],
        )
    )

    notes = (
        input(
            "Outcome notes [optional]: "
        ).strip()
        or None
    )

    service = TradeOutcomeService(
        store
    )

    try:

        closed = service.close_outcome(
            opportunity_id,
            exit_datetime=exit_datetime,
            exit_price=exit_price,
            exit_quantity=exit_quantity,
            exit_reason=exit_reason,
            exit_commission_eur=exit_commission_eur,
            exit_fx_to_eur=exit_fx_to_eur,
            notes=notes,
        )

    except ValueError as exc:

        print(
            "\nTradeOutcome close unavailable."
        )

        print(
            str(exc)
        )

        return

    print(
        "\nTradeOutcome closed and persisted."
    )

    _print_trade_outcome(
        closed
    )


# =============================================================
# Argument parser
# =============================================================


def build_parser(
) -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog=(
            "python -m app.cio.cli"
        ),
        description=(
            "Stage 3.0 local Account State "
            "and Fineco Instrument Cache CLI."
        ),
    )

    parser.add_argument(
        "--db",
        default=str(
            DEFAULT_DB_PATH
        ),
        help=(
            "SQLite state database path "
            f"(default: {DEFAULT_DB_PATH})"
        ),
    )

    top = parser.add_subparsers(
        dest="resource",
        required=True,
    )

    # ---------------------------------------------------------
    # Account
    # ---------------------------------------------------------

    account = top.add_parser(
        "account",
        help=(
            "Manage CIO Account State."
        ),
    )

    account_sub = (
        account.add_subparsers(
            dest="action",
            required=True,
        )
    )

    account_sub.add_parser(
        "show",
        help=(
            "Show latest Account State."
        ),
    )

    account_sub.add_parser(
        "set",
        help=(
            "Interactively create "
            "a new Account State."
        ),
    )

    # ---------------------------------------------------------
    # Instruments
    # ---------------------------------------------------------

    instruments = top.add_parser(
        "instruments",
        help=(
            "Manage Fineco "
            "Instrument Cache."
        ),
    )

    instrument_sub = (
        instruments.add_subparsers(
            dest="action",
            required=True,
        )
    )

    show_parser = (
        instrument_sub.add_parser(
            "show",
            help=(
                "Show cached instruments "
                "for one underlying."
            ),
        )
    )

    show_parser.add_argument(
        "underlying"
    )

    instrument_sub.add_parser(
        "list",
        help=(
            "List all cached "
            "Fineco instruments."
        ),
    )

    instrument_sub.add_parser(
        "add",
        help=(
            "Interactively add one "
            "Fineco instrument/mode."
        ),
    )

    edit_parser = (
        instrument_sub.add_parser(
            "edit",
            help=(
                "Edit an existing Fineco "
                "instrument while preserving "
                "unchanged values."
            ),
        )
    )

    edit_parser.add_argument(
        "instrument_id"
    )

    delete_parser = (
        instrument_sub.add_parser(
            "delete",
            help=(
                "Delete one Fineco instrument "
                "from the local cache."
            ),
        )
    )

    delete_parser.add_argument(
        "instrument_id"
    )

    import_parser = (
        instrument_sub.add_parser(
        "import",
        help=(
            "Bulk import Fineco instruments "
            "from a JSON file."
            ),
        )
    )

    import_parser.add_argument(
        "file",
         help=(
            "JSON file containing Fineco "
            "instrument definitions."
        ),
    )

    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate and preview changes "
            "without modifying the database."
        ),
    )

    # ---------------------------------------------------------
    # Opportunities
    # ---------------------------------------------------------

    opportunities = top.add_parser(
        "opportunities",
        help=(
            "Manage CIO trade opportunities."
        ),
    )

    opportunity_sub = (
        opportunities.add_subparsers(
            dest="action",
            required=True,
        )
    )

    opportunity_sub.add_parser(
        "add",
        help=(
            "Interactively create a trade "
            "opportunity for testing."
        ),
    )

    opportunity_list_parser = (
        opportunity_sub.add_parser(
            "list",
            help=(
                "List persisted CIO "
                "trade opportunities."
            ),
        )
    )

    opportunity_list_parser.add_argument(
        "--status",
        choices=[
            item.value
            for item
            in OpportunityStatus
        ],
        help=(
            "Filter by opportunity status."
        ),
    )

    opportunity_show_parser = (
        opportunity_sub.add_parser(
            "show",
            help=(
                "Show one trade opportunity."
            ),
        )
    )

    opportunity_show_parser.add_argument(
        "opportunity_id"
    )

    opportunity_refresh_parser = (
        opportunity_sub.add_parser(
            "refresh",
            help=(
                "Refresh Fineco broker state "
                "for one opportunity."
            ),
        )
    )

    opportunity_refresh_parser.add_argument(
        "opportunity_id"
    )

    opportunity_sub.add_parser(
        "refresh-waiting",
        help=(
            "Refresh all opportunities waiting "
            "for Fineco broker instruments."
        ),
    )

    opportunity_filter_parser = (
        opportunity_sub.add_parser(
            "filter",
            help=(
                "Run and persist the canonical Portfolio Filter "
                "for one TradeOpportunity."
            ),
        )
    )

    opportunity_filter_parser.add_argument(
        "opportunity_id",
        help=(
            "Persisted TradeOpportunity ID."
        ),
    )

    opportunity_filter_parser.add_argument(
        "--assessment-id",
        dest="assessment_id",
        default=None,
        help=(
            "Optional explicit PortfolioFitAssessment ID. "
            "If omitted, the service generates one."
        ),
    )

    opportunity_filter_show_parser = (
        opportunity_sub.add_parser(
            "filter-show",
            help=(
                "Show one exact persisted PortfolioFitAssessment."
            ),
        )
    )

    opportunity_filter_show_parser.add_argument(
        "assessment_id",
        help=(
            "Persisted PortfolioFitAssessment ID."
        ),
    )

    opportunity_instruments_parser = (
        opportunity_sub.add_parser(
            "instruments",
            help=(
                "Run the Instrument Selector for "
                "one persisted trade opportunity."
            ),
        )
    )

    opportunity_instruments_parser.add_argument(
        "opportunity_id",
        help=(
            "Persisted TradeOpportunity ID."
        ),
    )

    opportunity_instruments_parser.add_argument(
        "--assessment-id",
        dest="assessment_id",
        required=True,
        help=(
            "Exact persisted PortfolioFitAssessment ID authorizing "
            "lifecycle advancement to Instrument Selection."
        ),
    )

    opportunity_candidates_parser = (
        opportunity_sub.add_parser(
            "candidates",
            help=(
                "Show persisted Instrument Selector "
                "results for one opportunity."
            ),
        )
    )

    opportunity_candidates_parser.add_argument(
        "opportunity_id"
    )

    opportunity_size_parser = (
        opportunity_sub.add_parser(
            "size",
            help=(
                "Size the highest-ranked persisted "
                "instrument candidate, or override it "
                "with a specific eligible candidate."
            ),
        )
    )

    opportunity_size_parser.add_argument(
        "opportunity_id"
    )

    opportunity_size_parser.add_argument(
        "--instrument-id",
        dest="instrument_id",
        default=None,
        help=(
            "Override the highest-ranked eligible "
            "instrument candidate with a specific "
            "persisted eligible InstrumentCandidate ID."
        ),
    )

    opportunity_propose_parser = (
        opportunity_sub.add_parser(
            "propose",
            help=(
                "Build and persist a preliminary TradeProposal "
                "from the latest successful position sizing."
            ),
        )
    )

    opportunity_propose_parser.add_argument(
        "opportunity_id"
    )

    opportunity_proposal_parser = (
        opportunity_sub.add_parser(
            "proposal",
            help=(
                "Show the latest persisted TradeProposal "
                "for one opportunity."
            ),
        )
    )

    opportunity_proposal_parser.add_argument(
        "opportunity_id"
    )

    opportunity_simulate_parser = (
        opportunity_sub.add_parser(
            "simulate",
            help=(
                "Simulate the latest TradeProposal against "
                "the persisted portfolio snapshot."
            ),
        )
    )

    opportunity_simulate_parser.add_argument(
        "opportunity_id"
    )

    opportunity_simulation_parser = (
        opportunity_sub.add_parser(
            "simulation",
            help=(
                "Show the latest persisted PortfolioSimulation "
                "for one opportunity."
            ),
        )
    )

    opportunity_simulation_parser.add_argument(
        "opportunity_id"
    )

    opportunity_decide_parser = (
        opportunity_sub.add_parser(
            "decide",
            help=(
                "Run the CIO Decision Engine on the latest "
                "TradeProposal and PortfolioSimulation."
            ),
        )
    )

    opportunity_decide_parser.add_argument(
        "opportunity_id"
    )

    opportunity_decision_parser = (
        opportunity_sub.add_parser(
            "decision",
            help=(
                "Show the latest persisted CIO decision "
                "for one opportunity."
            ),
        )
    )

    opportunity_decision_parser.add_argument(
        "opportunity_id"
    )

    opportunity_execution_plan_parser = (
        opportunity_sub.add_parser(
            "execution-plan",
            help=(
                "Build and persist the broker-ready manual "
                "ExecutionPlan from the latest accepted CIO decision."
            ),
        )
    )

    opportunity_execution_plan_parser.add_argument(
        "opportunity_id"
    )

    opportunity_execution_parser = (
        opportunity_sub.add_parser(
            "execution",
            help=(
                "Show the latest persisted ExecutionPlan "
                "for one opportunity."
            ),
        )
    )

    opportunity_execution_parser.add_argument(
        "opportunity_id"
    )

    opportunity_confirm_parser = (
        opportunity_sub.add_parser(
            "confirm",
            help=(
                "Record the explicit operator outcome of the "
                "latest persisted ExecutionPlan."
            ),
        )
    )

    opportunity_confirm_parser.add_argument(
        "opportunity_id"
    )

    opportunity_confirmation_parser = (
        opportunity_sub.add_parser(
            "confirmation",
            help=(
                "Show the latest persisted OperatorConfirmation "
                "for one opportunity."
            ),
        )
    )

    opportunity_confirmation_parser.add_argument(
        "opportunity_id"
    )

    opportunity_outcome_parser = (
        opportunity_sub.add_parser(
            "outcome",
            help=(
                "Create or resolve the OPEN TradeOutcome for an "
                "operator-confirmed execution."
            ),
        )
    )

    opportunity_outcome_parser.add_argument(
        "opportunity_id"
    )

    opportunity_outcome_show_parser = (
        opportunity_sub.add_parser(
            "outcome-show",
            help=(
                "Show the latest persisted TradeOutcome."
            ),
        )
    )

    opportunity_outcome_show_parser.add_argument(
        "opportunity_id"
    )

    opportunity_outcome_close_parser = (
        opportunity_sub.add_parser(
            "outcome-close",
            help=(
                "Fully close the latest OPEN TradeOutcome."
            ),
        )
    )

    opportunity_outcome_close_parser.add_argument(
        "opportunity_id"
    )

    return parser

# =============================================================
# Main
# =============================================================


def main(
    argv: list[str] | None = None,
) -> None:

    parser = (
        build_parser()
    )

    args = parser.parse_args(
        argv
    )

    store = Stage3Store(
        args.db
    )

    if args.resource == "account":

        if args.action == "show":

            account_show(
                store
            )

        elif args.action == "set":

            account_set(
                store
            )

        return

    if args.resource == "instruments":

        if args.action == "show":

            instruments_show(
                store,
                args.underlying,
            )

        elif args.action == "list":

            instruments_list(
                store
            )

        elif args.action == "add":

            instrument_add(
                store
            )

        elif args.action == "edit":

            instrument_edit(
                store,
                args.instrument_id,
            )

        elif args.action == "delete":

            instrument_delete(
                store,
                args.instrument_id,
            )

        elif args.action == "import":

            importer = FinecoBulkImporter(
                store
            )

            result = importer.import_file(
                args.file,
                dry_run=args.dry_run,
            )

            print_bulk_import_result(
                 result
            )

        return

    if args.resource == "opportunities":

        if args.action == "add":

            opportunity_add(
                store
            )

        elif args.action == "list":

            opportunities_list(
                store,
                status=args.status,
            )

        elif args.action == "show":

            opportunity_show(
                store,
                args.opportunity_id,
            )

        elif args.action == "refresh":

            opportunity_refresh(
                store,
                args.opportunity_id,
            )

        elif (
            args.action
            == "refresh-waiting"
        ):

            opportunities_refresh_waiting(
                store
            )

        elif args.action == "filter":

            assess_portfolio_fit_cli(
                store,
                args.opportunity_id,
                assessment_id=args.assessment_id,
            )

        elif args.action == "filter-show":

            show_portfolio_fit_cli(
                store,
                args.assessment_id,
            )

        elif args.action == "instruments":

            opportunity_instruments(
                store,
                args.opportunity_id,
                args.assessment_id,
            )

        elif args.action == "candidates":

            opportunity_candidates(
                store,
                args.opportunity_id,
            )

        elif args.action == "size":

            opportunity_size(
                store,
                args.opportunity_id,
                instrument_id=args.instrument_id,
            )

        elif args.action == "propose":

            opportunity_propose(
                store,
                args.opportunity_id,
            )

        elif args.action == "proposal":

            opportunity_proposal(
                store,
                args.opportunity_id,
            )

        elif args.action == "simulate":

            opportunity_simulate(
                store,
                args.opportunity_id,
            )

        elif args.action == "simulation":

            opportunity_simulation(
                store,
                args.opportunity_id,
            )

        elif args.action == "decide":

            opportunity_decide(
                store,
                args.opportunity_id,
            )

        elif args.action == "decision":

            opportunity_decision(
                store,
                args.opportunity_id,
            )

        elif args.action == "execution-plan":

            opportunity_execution_plan(
                store,
                args.opportunity_id,
            )

        elif args.action == "execution":

            opportunity_execution(
                store,
                args.opportunity_id,
            )

        elif args.action == "confirm":

            opportunity_confirm(
                store,
                args.opportunity_id,
            )

        elif args.action == "confirmation":

            opportunity_confirmation(
                store,
                args.opportunity_id,
            )

        elif args.action == "outcome":

            opportunity_outcome(
                store,
                args.opportunity_id,
            )

        elif args.action == "outcome-show":

            opportunity_outcome_show(
                store,
                args.opportunity_id,
            )

        elif args.action == "outcome-close":

            opportunity_outcome_close(
                store,
                args.opportunity_id,
            )

        return

if __name__ == "__main__":
    main()