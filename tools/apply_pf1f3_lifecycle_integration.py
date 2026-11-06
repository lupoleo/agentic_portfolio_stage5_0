from __future__ import annotations

from pathlib import Path

CLI_PATH = Path("app/cio/cli.py")


def require_once(text: str, needle: str, description: str) -> None:
    count = text.count(needle)
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one {description}; found {count}. "
            "No changes were written."
        )


def main() -> None:
    if not CLI_PATH.exists():
        raise RuntimeError(
            "Run this script from the repository root; "
            "app/cio/cli.py was not found."
        )

    original = CLI_PATH.read_text(encoding="utf-8")
    text = original

    # ---------------------------------------------------------
    # 1. Import lifecycle gate contracts.
    #
    # PF-1F.2 already proved this exact Stage3Store import anchor
    # exists in the real cli.py, so use that instead of assuming
    # a one-line InstrumentSelector import.
    # ---------------------------------------------------------

    import_anchor = "from app.cio.storage import Stage3Store\n"
    require_once(
        text,
        import_anchor,
        "Stage3Store import anchor",
    )

    import_block = (
        "from app.cio.portfolio_lifecycle_gate import (\n"
        "    PortfolioLifecycleAction,\n"
        "    PortfolioLifecycleGate,\n"
        ")\n"
    )

    if import_block not in text:
        text = text.replace(
            import_anchor,
            import_block + import_anchor,
            1,
        )

    # ---------------------------------------------------------
    # 2. Require explicit assessment_id in opportunity_instruments.
    # ---------------------------------------------------------

    old_signature = (
        "def opportunity_instruments(\n"
        "    store: Stage3Store,\n"
        "    opportunity_id: str,\n"
        ") -> None:\n"
    )
    new_signature = (
        "def opportunity_instruments(\n"
        "    store: Stage3Store,\n"
        "    opportunity_id: str,\n"
        "    assessment_id: str,\n"
        ") -> None:\n"
    )

    if old_signature in text:
        text = text.replace(old_signature, new_signature, 1)
    elif new_signature not in text:
        raise RuntimeError(
            "Could not find opportunity_instruments signature. "
            "No changes were written."
        )

    # ---------------------------------------------------------
    # 3. Insert gate before broker refresh / InstrumentSelector.
    # ---------------------------------------------------------

    refresh_anchor = (
        "    # ---------------------------------------------------------\n"
        "    # Refresh broker/cache state before selection.\n"
        "    # ---------------------------------------------------------\n\n"
        "    refreshed = (\n"
    )
    require_once(
        text,
        refresh_anchor,
        "broker refresh anchor inside opportunity_instruments",
    )

    gate_block = (
        "    # ---------------------------------------------------------\n"
        "    # Portfolio lifecycle gate.\n"
        "    # ---------------------------------------------------------\n\n"
        "    lifecycle_gate = PortfolioLifecycleGate(\n"
        "        store\n"
        "    )\n\n"
        "    gate_result = lifecycle_gate.evaluate(\n"
        "        assessment_id\n"
        "    )\n\n"
        "    if (\n"
        "        gate_result.opportunity_id\n"
        "        != opportunity.opportunity_id\n"
        "    ):\n"
        "        raise ValueError(\n"
        "            \"PortfolioFitAssessment does not belong to the \"\n"
        "            \"requested TradeOpportunity: \"\n"
        "            f\"{assessment_id} -> {gate_result.opportunity_id}, \"\n"
        "            f\"requested={opportunity.opportunity_id}\"\n"
        "        )\n\n"
        "    print(\n"
        "        \"\\n=== CIO PORTFOLIO LIFECYCLE GATE ===\\n\"\n"
        "    )\n\n"
        "    print(f\"Assessment: {gate_result.assessment_id}\")\n"
        "    print(\n"
        "        f\"Portfolio decision: \"\n"
        "        f\"{gate_result.portfolio_fit_decision.value}\"\n"
        "    )\n"
        "    print(f\"Lifecycle action: {gate_result.action.value}\")\n"
        "    print(f\"Reason: {gate_result.reason_code.value}\")\n\n"
        "    if gate_result.warnings:\n"
        "        print(\"\\nLifecycle warnings\")\n"
        "        print(\"-\" * 60)\n"
        "        for warning in gate_result.warnings:\n"
        "            print(f\"- {warning}\")\n\n"
        "    if not gate_result.can_advance:\n"
        "        print(\"\\nInstrument selection blocked.\")\n"
        "        print(gate_result.reason)\n"
        "        return\n\n"
        "    if (\n"
        "        gate_result.action\n"
        "        == PortfolioLifecycleAction.ADVANCE_WITH_WARNING\n"
        "    ):\n"
        "        print(\n"
        "            \"\\nInstrument selection allowed \"\n"
        "            \"with portfolio warnings.\"\n"
        "        )\n"
        "    else:\n"
        "        print(\"\\nInstrument selection allowed.\")\n\n"
    )

    if "=== CIO PORTFOLIO LIFECYCLE GATE ===" not in text:
        text = text.replace(
            refresh_anchor,
            gate_block + refresh_anchor,
            1,
        )

    # ---------------------------------------------------------
    # 4. Parser: explicit required --assessment-id.
    # ---------------------------------------------------------

    parser_arg_anchor = (
        "    opportunity_instruments_parser.add_argument(\n"
        "        \"opportunity_id\",\n"
        "        help=(\n"
        "            \"Persisted TradeOpportunity ID.\"\n"
        "        ),\n"
        "    )\n"
    )
    require_once(
        text,
        parser_arg_anchor,
        "opportunity instruments opportunity_id argument",
    )

    assessment_arg = (
        "\n"
        "    opportunity_instruments_parser.add_argument(\n"
        "        \"--assessment-id\",\n"
        "        dest=\"assessment_id\",\n"
        "        required=True,\n"
        "        help=(\n"
        "            \"Exact persisted PortfolioFitAssessment ID authorizing \"\n"
        "            \"lifecycle advancement to Instrument Selection.\"\n"
        "        ),\n"
        "    )\n"
    )

    if (
        'opportunity_instruments_parser.add_argument(\n'
        '        "--assessment-id",' not in text
    ):
        text = text.replace(
            parser_arg_anchor,
            parser_arg_anchor + assessment_arg,
            1,
        )

    # ---------------------------------------------------------
    # 5. Dispatch: forward assessment_id.
    # ---------------------------------------------------------

    old_call = (
        "            opportunity_instruments(\n"
        "                store,\n"
        "                args.opportunity_id,\n"
        "            )\n"
    )
    new_call = (
        "            opportunity_instruments(\n"
        "                store,\n"
        "                args.opportunity_id,\n"
        "                args.assessment_id,\n"
        "            )\n"
    )

    if old_call in text:
        text = text.replace(old_call, new_call, 1)
    elif new_call not in text:
        raise RuntimeError(
            "Could not find opportunity_instruments dispatch call. "
            "No changes were written."
        )

    if text == original:
        print("PF-1F.3 integration already appears to be applied.")
        return

    backup = CLI_PATH.with_suffix(".py.pf1f3.bak")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    CLI_PATH.write_text(text, encoding="utf-8")

    print("PF-1F.3 persisted-assessment lifecycle integration applied.")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
