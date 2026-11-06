from __future__ import annotations

from pathlib import Path
import subprocess
import sys


CLI_PATH = Path("app/cio/cli.py")


def _cli_text() -> str:
    assert CLI_PATH.exists(), "app/cio/cli.py not found"
    return CLI_PATH.read_text(encoding="utf-8")


def _instrument_function(text: str) -> str:
    start = text.index("def opportunity_instruments(")
    end = text.find("\ndef ", start + 1)
    if end == -1:
        end = len(text)
    return text[start:end]


def test_pf1f4_cli_requires_exact_persisted_assessment():
    text = _cli_text()

    assert (
        'opportunity_instruments_parser.add_argument(\n'
        '        "--assessment-id",\n'
        '        dest="assessment_id",\n'
        '        required=True,\n'
    ) in text

    assert (
        "            opportunity_instruments(\n"
        "                store,\n"
        "                args.opportunity_id,\n"
        "                args.assessment_id,\n"
        "            )\n"
    ) in text


def test_pf1f4_no_implicit_latest_assessment_fallback():
    fn = _instrument_function(_cli_text())

    assert "get_latest_portfolio_fit_assessment" not in fn
    assert "PortfolioLifecycleGate(" in fn
    assert "gate_result = lifecycle_gate.evaluate(" in fn


def test_pf1f4_gate_precedes_any_instrument_selection_side_effect():
    fn = _instrument_function(_cli_text())

    gate_pos = fn.index("PortfolioLifecycleGate(")
    block_pos = fn.index("if not gate_result.can_advance:")
    refresh_pos = fn.index("# Refresh broker/cache state before selection.")
    selector_pos = fn.index("InstrumentSelector()")

    assert gate_pos < block_pos < refresh_pos < selector_pos

    blocked_region = fn[block_pos:refresh_pos]
    assert "return" in blocked_region


def test_pf1f4_assessment_opportunity_identity_is_enforced():
    fn = _instrument_function(_cli_text())

    assert (
        "gate_result.opportunity_id\n"
        "        != opportunity.opportunity_id"
    ) in fn
    assert "PortfolioFitAssessment does not belong to the " in fn


def test_pf1f4_warning_path_can_advance():
    fn = _instrument_function(_cli_text())

    assert "PortfolioLifecycleAction.ADVANCE_WITH_WARNING" in fn
    assert "Instrument selection allowed " in fn
    assert "with portfolio warnings." in fn


def test_pf1f4_block_path_is_operator_visible():
    fn = _instrument_function(_cli_text())

    assert "Instrument selection blocked." in fn
    assert "gate_result.reason" in fn


def test_pf1f4_filter_and_instruments_are_separate_cli_stages():
    # Validate only the stable public contract: each command exists as its own
    # argparse subcommand. Do not assert exact help prose, since argparse may
    # wrap/truncate it differently across source versions or terminal widths.
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cio.cli",
            "opportunities",
            "--help",
        ],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr

    out = completed.stdout

    # The usage choice-list is the stable evidence that these are distinct
    # registered subcommands.
    assert "filter" in out
    assert "filter-show" in out
    assert "instruments" in out

    # And the instruments stage must not contain the filter-only terminal text.
    assert (
        "No lifecycle advancement was performed."
        not in _instrument_function(_cli_text())
    )


def test_pf1f4_gate_is_before_broker_refresh_not_after():
    fn = _instrument_function(_cli_text())

    gate_pos = fn.index("gate_result = lifecycle_gate.evaluate(")
    refresh_pos = fn.index("# Refresh broker/cache state before selection.")

    assert gate_pos < refresh_pos
