from __future__ import annotations

from pathlib import Path

CLI_PATH = Path("app/cio/cli.py")


def _read_cli() -> str:
    assert CLI_PATH.exists()
    return CLI_PATH.read_text(encoding="utf-8")


def test_instruments_requires_explicit_assessment_id_in_signature():
    text = _read_cli()
    assert (
        "def opportunity_instruments(\n"
        "    store: Stage3Store,\n"
        "    opportunity_id: str,\n"
        "    assessment_id: str,\n"
        ") -> None:\n"
    ) in text


def test_instruments_cli_requires_assessment_id():
    text = _read_cli()
    assert (
        'opportunity_instruments_parser.add_argument(\n'
        '        "--assessment-id",\n'
        '        dest="assessment_id",\n'
        '        required=True,\n'
    ) in text


def test_instruments_uses_portfolio_lifecycle_gate():
    text = _read_cli()
    assert "PortfolioLifecycleGate(" in text
    assert "gate_result = lifecycle_gate.evaluate(" in text


def test_instruments_fails_closed_before_broker_refresh():
    text = _read_cli()
    start = text.index("def opportunity_instruments(")
    gate = text.index("if not gate_result.can_advance:", start)
    refresh = text.index(
        "# Refresh broker/cache state before selection.",
        start,
    )
    assert gate < refresh
    assert "return" in text[gate:refresh]


def test_requested_opportunity_must_match_assessment_opportunity():
    text = _read_cli()
    assert (
        "gate_result.opportunity_id\n"
        "        != opportunity.opportunity_id"
    ) in text


def test_dispatch_forwards_assessment_id():
    text = _read_cli()
    assert (
        "            opportunity_instruments(\n"
        "                store,\n"
        "                args.opportunity_id,\n"
        "                args.assessment_id,\n"
        "            )\n"
    ) in text


def test_no_implicit_latest_portfolio_fit_lookup_in_instrument_command():
    text = _read_cli()
    start = text.index("def opportunity_instruments(")
    end = text.find("\ndef ", start + 1)
    if end == -1:
        end = len(text)
    function_text = text[start:end]
    assert "get_latest_portfolio_fit_assessment" not in function_text


def test_gate_runs_before_instrument_selector():
    text = _read_cli()
    start = text.index("def opportunity_instruments(")
    gate = text.index("PortfolioLifecycleGate(", start)
    selector = text.index("InstrumentSelector()", start)
    assert gate < selector
