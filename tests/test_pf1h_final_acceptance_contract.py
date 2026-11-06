from __future__ import annotations

from pathlib import Path


def _script_text() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "pf1h_final_acceptance_gate.py"
    ).read_text(encoding="utf-8")


def test_pf1h_gate_bootstraps_repo_root_for_direct_execution():
    text = _script_text()
    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in text
    assert "sys.path.insert(0, str(REPO_ROOT))" in text


def test_pf1h_gate_uses_both_canonical_composition_roots():
    text = _script_text()
    assert "build_canonical_portfolio_filter_service" in text
    assert "build_canonical_portfolio_simulation_service" in text


def test_pf1h_gate_is_read_only_by_contract():
    text = _script_text()
    forbidden = [
        ".save_portfolio_fit_assessment(",
        ".save_trade_proposal(",
        ".save_portfolio_simulation(",
        ".save_trade_opportunity(",
        "assess_and_persist(",
    ]
    for token in forbidden:
        assert token not in text


def test_pf1h_gate_checks_shared_quantitative_consistency():
    text = _script_text()
    assert "PF and Simulator V2 share quantitative truth" in text
    assert "ctx.volatility_pct.after" in text
    assert "sim.after.portfolio_volatility_pct" in text
    assert "ctx.beta.after" in text
    assert "sim.after.portfolio_beta" in text
    assert "ctx.var_95_1d_eur.after" in text
    assert "sim.after.var_95_1d_eur" in text
