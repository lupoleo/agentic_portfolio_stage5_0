import json

from tools.fundamental_variance_outlier_diagnostics import (
    _extract_component,
    _parse_variance_payload,
)


def test_parse_variance_boundary():
    text = (
        "=== AI-8C.3d.1 VARIANCE JSON ===\n"
        '{"ticker":"MRVL","selected_evidence_ids":["E1"]}\n'
        "=== END AI-8C.3d.1 VARIANCE JSON ==="
    )
    assert _parse_variance_payload(text)["ticker"] == "MRVL"


def test_extract_fundamental_component():
    text = """  FUNDAMENTAL   score=52.50
                evidence=E1, E2
                rationale=Growth is strong, but margins are unknown.
  TECHNICAL     score=50.00
                evidence=E1
                rationale=Mixed.
=== SCORABILITY PROVENANCE ===
"""
    item = _extract_component(text, "fundamental")
    assert item["score"] == 52.5
    assert item["evidence"] == "E1, E2"
    assert "margins are unknown" in item["rationale"]
