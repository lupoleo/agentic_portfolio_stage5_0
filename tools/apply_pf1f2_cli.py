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

    import_anchor = "from app.cio.storage import Stage3Store\n"
    require_once(
        text,
        import_anchor,
        "Stage3Store import anchor",
    )

    import_block = (
        "from app.cio.portfolio_filter_cli import (\n"
        "    assess_portfolio_fit_cli,\n"
        "    show_portfolio_fit_cli,\n"
        ")\n"
    )

    if import_block not in text:
        text = text.replace(
            import_anchor,
            import_block + import_anchor,
            1,
        )

    parser_anchor = (
        "    opportunity_instruments_parser = (\n"
        "        opportunity_sub.add_parser(\n"
        '            "instruments",\n'
    )
    require_once(
        text,
        parser_anchor,
        "opportunity instruments parser anchor",
    )

    parser_block = '''    opportunity_filter_parser = (
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

'''

    if '"filter-show"' not in text:
        text = text.replace(
            parser_anchor,
            parser_block + parser_anchor,
            1,
        )

    dispatch_anchor_candidates = [
        '''        elif args.action == "instruments":

            opportunity_instruments(
''',
        '''        if args.action == "instruments":

            opportunity_instruments(
''',
    ]

    dispatch_anchor = None
    for candidate in dispatch_anchor_candidates:
        if candidate in text:
            dispatch_anchor = candidate
            break

    if dispatch_anchor is None:
        raise RuntimeError(
            "Could not find the opportunities/instruments dispatch anchor. "
            "No changes were written."
        )

    dispatch_block = '''        elif args.action == "filter":

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

'''

    if 'args.action == "filter"' not in text:
        if dispatch_anchor.startswith("        if "):
            first = dispatch_block.replace(
                '        elif args.action == "filter":',
                '        if args.action == "filter":',
                1,
            )
            text = text.replace(
                dispatch_anchor,
                first + dispatch_anchor,
                1,
            )
        else:
            text = text.replace(
                dispatch_anchor,
                dispatch_block + dispatch_anchor,
                1,
            )

    if text == original:
        print("PF-1F.2 CLI integration already appears to be applied.")
        return

    backup = CLI_PATH.with_suffix(".py.pf1f2.bak")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    CLI_PATH.write_text(text, encoding="utf-8")

    print("PF-1F.2 CLI integration applied.")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
