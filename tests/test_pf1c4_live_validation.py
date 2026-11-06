from types import SimpleNamespace

from app.cio.pf1c4_live_validation import (
    ValidationTolerance,
    validate_before_state,
)


def metric(before):
    return SimpleNamespace(
        before=before,
        after=before,
        delta=0.0,
    )


def position(direction, exposure):
    return SimpleNamespace(
        position=SimpleNamespace(
            direction=direction,
            market_value_eur=exposure,
        )
    )


def baseline():
    snapshot = SimpleNamespace(
        analyzed_positions=2,
        gross_exposure_eur=100_000.0,
        net_exposure_eur=60_000.0,
    )

    risk_record = SimpleNamespace(
        state=SimpleNamespace(
            portfolio_volatility_pct=15.0,
            portfolio_beta=0.8,
            var_95_1d_eur=4_000.0,
            cvar_95_1d_eur=6_000.0,
            analytical_coverage_pct=90.0,
        )
    )

    result = SimpleNamespace(
        volatility_pct=metric(15.0),
        beta=metric(0.8),
        var_95_1d_eur=metric(4_000.0),
        cvar_95_1d_eur=metric(6_000.0),
        analytical_coverage_before_pct=90.0,
    )

    positions = [
        position("LONG", 80_000),
        position("SHORT", 20_000),
    ]

    return snapshot, risk_record, result, positions


def test_exact_before_state_passes():
    snapshot, risk, result, positions = baseline()

    checks = validate_before_state(
        snapshot=snapshot,
        persisted_risk_state=risk,
        reconstructed_positions=positions,
        marginal_result=result,
    )

    assert all(check.passed for check in checks)


def test_source_file_portfolio_drift_fails_identity_gate():
    snapshot, risk, result, positions = baseline()
    positions[0].position.market_value_eur = 81_000

    checks = validate_before_state(
        snapshot=snapshot,
        persisted_risk_state=risk,
        reconstructed_positions=positions,
        marginal_result=result,
    )

    by_name = {c.name: c for c in checks}
    assert not by_name["gross_exposure"].passed
    assert not by_name["net_exposure"].passed


def test_quantitative_drift_fails_before_gate():
    snapshot, risk, result, positions = baseline()
    result.volatility_pct.before = 15.5

    checks = validate_before_state(
        snapshot=snapshot,
        persisted_risk_state=risk,
        reconstructed_positions=positions,
        marginal_result=result,
    )

    by_name = {c.name: c for c in checks}
    assert not by_name["portfolio_volatility"].passed


def test_small_market_data_revision_is_within_tolerance():
    snapshot, risk, result, positions = baseline()
    result.var_95_1d_eur.before = 4_050.0
    result.cvar_95_1d_eur.before = 6_080.0
    result.beta.before = 0.81
    result.volatility_pct.before = 15.05

    checks = validate_before_state(
        snapshot=snapshot,
        persisted_risk_state=risk,
        reconstructed_positions=positions,
        marginal_result=result,
    )

    assert all(check.passed for check in checks)


def test_position_count_mismatch_fails():
    snapshot, risk, result, positions = baseline()
    snapshot.analyzed_positions = 3

    checks = validate_before_state(
        snapshot=snapshot,
        persisted_risk_state=risk,
        reconstructed_positions=positions,
        marginal_result=result,
    )

    by_name = {c.name: c for c in checks}
    assert not by_name["position_count"].passed
