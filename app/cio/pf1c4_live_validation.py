from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from app.analysis.marginal_risk import PortfolioMarginalRiskEngine
from app.analysis.portfolio import (
    BENCHMARK_SYMBOL,
    RISK_MAX_SESSIONS,
    RISK_MIN_COMMON_OBSERVATIONS,
    STAGE3_DB_PATH,
)
from app.cio.portfolio_marginal_risk_provider import (
    CanonicalPortfolioMarginalRiskInputProvider,
)
from app.cio.storage import Stage3Store


@dataclass(frozen=True)
class ValidationTolerance:
    volatility_pp: float = 0.10
    beta: float = 0.02
    var_relative: float = 0.015
    var_absolute_eur: float = 75.0
    cvar_relative: float = 0.015
    cvar_absolute_eur: float = 100.0
    coverage_pp: float = 0.10
    exposure_eur: float = 0.05


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    expected: float
    actual: float
    tolerance: float
    passed: bool
    unit: str = ""


def _relative_or_absolute_tolerance(
    expected: float,
    *,
    relative: float,
    absolute: float,
) -> float:
    return max(abs(expected) * relative, absolute)


def _check(
    name: str,
    expected: float,
    actual: float,
    tolerance: float,
    unit: str = "",
) -> ValidationCheck:
    return ValidationCheck(
        name=name,
        expected=float(expected),
        actual=float(actual),
        tolerance=float(tolerance),
        passed=abs(float(actual) - float(expected)) <= float(tolerance),
        unit=unit,
    )


def _reconstructed_exposures(analyzed_positions):
    long_eur = 0.0
    short_eur = 0.0

    for item in analyzed_positions:
        direction = str(item.position.direction).upper()
        exposure = abs(float(item.position.market_value_eur))

        if direction == "LONG":
            long_eur += exposure
        elif direction == "SHORT":
            short_eur += exposure

    gross_eur = long_eur + short_eur
    net_eur = long_eur - short_eur
    return long_eur, short_eur, gross_eur, net_eur


def validate_before_state(
    *,
    snapshot,
    persisted_risk_state,
    reconstructed_positions,
    marginal_result,
    tolerance: ValidationTolerance | None = None,
) -> tuple[ValidationCheck, ...]:
    """
    PF-1C.4 acceptance gate.

    The gate has two layers:
      1. Portfolio identity: the mutable snapshot source file must still
         reconstruct the same analyzed-position count/gross/net exposure.
      2. Quantitative identity: the shared marginal engine BEFORE state must
         reproduce the persisted Stage 2.x risk state within bounded
         tolerances.

    AFTER metrics are never accepted if BEFORE fails.
    """

    tol = tolerance or ValidationTolerance()
    state = persisted_risk_state.state

    _, _, gross_eur, net_eur = _reconstructed_exposures(
        reconstructed_positions
    )

    checks = [
        _check(
            "position_count",
            snapshot.analyzed_positions,
            len(reconstructed_positions),
            0.0,
            "count",
        ),
        _check(
            "gross_exposure",
            snapshot.gross_exposure_eur,
            gross_eur,
            tol.exposure_eur,
            "EUR",
        ),
        _check(
            "net_exposure",
            snapshot.net_exposure_eur,
            net_eur,
            tol.exposure_eur,
            "EUR",
        ),
        _check(
            "portfolio_volatility",
            state.portfolio_volatility_pct,
            marginal_result.volatility_pct.before,
            tol.volatility_pp,
            "pct",
        ),
        _check(
            "portfolio_beta",
            state.portfolio_beta,
            marginal_result.beta.before,
            tol.beta,
            "beta",
        ),
        _check(
            "var_95_1d",
            state.var_95_1d_eur,
            marginal_result.var_95_1d_eur.before,
            _relative_or_absolute_tolerance(
                state.var_95_1d_eur,
                relative=tol.var_relative,
                absolute=tol.var_absolute_eur,
            ),
            "EUR",
        ),
        _check(
            "cvar_95_1d",
            state.cvar_95_1d_eur,
            marginal_result.cvar_95_1d_eur.before,
            _relative_or_absolute_tolerance(
                state.cvar_95_1d_eur,
                relative=tol.cvar_relative,
                absolute=tol.cvar_absolute_eur,
            ),
            "EUR",
        ),
        _check(
            "analytical_coverage",
            state.analytical_coverage_pct,
            marginal_result.analytical_coverage_before_pct,
            tol.coverage_pp,
            "pct",
        ),
    ]

    return tuple(checks)


def _fmt(check: ValidationCheck) -> str:
    diff = check.actual - check.expected
    status = "PASS" if check.passed else "FAIL"

    if check.unit == "EUR":
        values = (
            f"expected={check.expected:,.2f} "
            f"actual={check.actual:,.2f} "
            f"delta={diff:+,.2f} "
            f"tol={check.tolerance:,.2f}"
        )
    elif check.unit in {"pct", "beta"}:
        values = (
            f"expected={check.expected:.6f} "
            f"actual={check.actual:.6f} "
            f"delta={diff:+.6f} "
            f"tol={check.tolerance:.6f}"
        )
    else:
        values = (
            f"expected={check.expected:g} "
            f"actual={check.actual:g} "
            f"delta={diff:+g}"
        )

    return f"{status:<4}  {check.name:<24} {values}"


def run_live_validation(
    *,
    snapshot_id: str,
    candidate_symbol: str,
    direction: str,
    candidate_exposure_eur: float,
    db_path: str | Path = STAGE3_DB_PATH,
) -> int:
    direction = direction.upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if candidate_exposure_eur <= 0:
        raise ValueError("candidate exposure must be positive")

    store = Stage3Store(db_path)

    snapshot = store.get_portfolio_snapshot(snapshot_id)
    if snapshot is None:
        raise ValueError(f"PortfolioSnapshot not found: {snapshot_id}")

    risk_record = store.get_latest_portfolio_risk_state(snapshot_id)
    if risk_record is None:
        raise ValueError(
            f"PortfolioRiskState not found for snapshot: {snapshot_id}"
        )

    latest_account = store.get_latest_account_state()

    provider = CanonicalPortfolioMarginalRiskInputProvider()
    provider_opportunity = SimpleNamespace(
        snapshot_id=snapshot.snapshot_id,
        ticker=candidate_symbol,
        direction=direction,
    )
    inputs = provider(provider_opportunity, snapshot)

    engine = PortfolioMarginalRiskEngine(
        benchmark_symbol=BENCHMARK_SYMBOL,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )

    result = engine.assess(
        analyzed_positions=inputs.analyzed_positions,
        candidate_symbol=inputs.candidate_symbol,
        direction=direction,
        candidate_exposure_eur=candidate_exposure_eur,
        candidate_history=inputs.candidate_history,
        benchmark_history=inputs.benchmark_history,
    )

    print("\n=== PF-1C.4 LIVE VALIDATION ===\n")
    print(f"Snapshot:          {snapshot.snapshot_id}")
    print(f"Risk state:        {risk_record.risk_state_id}")
    if latest_account is not None:
        print(f"Latest account:    {latest_account.account_state_id}")
        nav = latest_account.canonical_nav_eur
        if nav is not None:
            print(f"Latest NAV:        EUR {nav:,.2f}")
    print(f"Candidate:         {candidate_symbol} {direction}")
    print(f"Candidate exposure EUR {candidate_exposure_eur:,.2f}")
    print()

    checks = validate_before_state(
        snapshot=snapshot,
        persisted_risk_state=risk_record,
        reconstructed_positions=inputs.analyzed_positions,
        marginal_result=result,
    )

    print("--- BEFORE acceptance gate ---\n")
    for check in checks:
        print(_fmt(check))

    passed = all(check.passed for check in checks)

    print()
    print(
        "PF-1C.4 BEFORE GATE: "
        + ("PASS" if passed else "FAIL")
    )

    if not passed:
        print(
            "\nAFTER metrics are NOT accepted because the reconstructed "
            "BEFORE state does not match the persisted Quant baseline."
        )
        return 2

    print("\n--- Accepted marginal AFTER state ---\n")
    print(
        f"Volatility: {result.volatility_pct.before:.4f}% -> "
        f"{result.volatility_pct.after:.4f}% "
        f"({result.volatility_pct.delta:+.4f} pp)"
    )
    print(
        f"Beta:       {result.beta.before:.4f} -> "
        f"{result.beta.after:.4f} "
        f"({result.beta.delta:+.4f})"
    )
    print(
        f"VaR95 1D:   EUR {result.var_95_1d_eur.before:,.2f} -> "
        f"EUR {result.var_95_1d_eur.after:,.2f} "
        f"({result.var_95_1d_eur.delta:+,.2f})"
    )
    print(
        f"CVaR95 1D:  EUR {result.cvar_95_1d_eur.before:,.2f} -> "
        f"EUR {result.cvar_95_1d_eur.after:,.2f} "
        f"({result.cvar_95_1d_eur.delta:+,.2f})"
    )

    corr = result.candidate_correlation_to_portfolio
    print(
        "Candidate correlation to portfolio: "
        + ("UNKNOWN" if corr is None else f"{corr:.4f}")
    )

    comp = result.candidate_component_risk_pct_points
    contr = result.candidate_risk_contribution_pct
    print(
        "Candidate component risk: "
        + ("UNKNOWN" if comp is None else f"{comp:+.4f} pp")
    )
    print(
        "Candidate risk contribution: "
        + ("UNKNOWN" if contr is None else f"{contr:+.2f}%")
    )

    print(
        f"Analytical coverage: "
        f"{result.analytical_coverage_before_pct:.2f}% -> "
        f"{result.analytical_coverage_after_pct:.2f}%"
    )
    print(
        "Excluded BEFORE: "
        + (
            ", ".join(result.excluded_symbols_before)
            if result.excluded_symbols_before
            else "none"
        )
    )
    print(
        "Excluded AFTER:  "
        + (
            ", ".join(result.excluded_symbols_after)
            if result.excluded_symbols_after
            else "none"
        )
    )
    print(
        f"Observations: {result.observations_before} -> "
        f"{result.observations_after}"
    )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PF-1C.4 live marginal-risk validation"
    )
    parser.add_argument(
        "--snapshot",
        default="SNAP-20260910-194305-499c14",
        help="PortfolioSnapshot ID",
    )
    parser.add_argument(
        "--candidate",
        default="QCOM",
        help="Yahoo candidate risk-factor symbol",
    )
    parser.add_argument(
        "--direction",
        choices=("LONG", "SHORT"),
        default="LONG",
    )
    parser.add_argument(
        "--exposure-eur",
        type=float,
        default=10_000.0,
    )
    parser.add_argument(
        "--db",
        default=str(STAGE3_DB_PATH),
    )

    args = parser.parse_args()

    return run_live_validation(
        snapshot_id=args.snapshot,
        candidate_symbol=args.candidate,
        direction=args.direction,
        candidate_exposure_eur=args.exposure_eur,
        db_path=args.db,
    )


if __name__ == "__main__":
    raise SystemExit(main())
