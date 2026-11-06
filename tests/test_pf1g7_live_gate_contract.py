from __future__ import annotations

import runpy
from pathlib import Path


def test_pf1g7_live_gate_has_direct_script_repo_root_bootstrap():
    path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "pf1g7_live_gate.py"
    )
    text = path.read_text(encoding="utf-8")

    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in text
    assert "sys.path.insert(0, str(REPO_ROOT))" in text


def test_pf1g7_live_gate_is_read_only_by_contract():
    path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "pf1g7_live_gate.py"
    )
    text = path.read_text(encoding="utf-8")

    forbidden = [
        ".save_trade_proposal(",
        ".save_portfolio_simulation(",
        ".save_trade_opportunity(",
        ".mark_trade_opportunity_",
    ]

    for token in forbidden:
        assert token not in text


def test_pf1g7_live_gate_uses_canonical_v2_factory():
    path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "pf1g7_live_gate.py"
    )
    text = path.read_text(encoding="utf-8")

    assert "build_canonical_portfolio_simulation_service" in text
    assert "simulator.simulate(" in text
    assert "marginal_risk_adapter is not None" in text


def test_pf1g7_live_gate_validates_both_qcom_directions():
    path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "pf1g7_live_gate.py"
    )
    text = path.read_text(encoding="utf-8")

    assert "Direction.LONG" in text
    assert "Direction.SHORT" in text
    assert "QCOM LONG" in text
    assert "QCOM SHORT" in text
