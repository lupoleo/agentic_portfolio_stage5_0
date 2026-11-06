from __future__ import annotations

from pathlib import Path


CLI_PATH = Path("app/cio/cli.py")

IMPORT_BLOCK = (
    "from app.cio.portfolio_simulation_factory import (\n"
    "    build_canonical_portfolio_simulation_service,\n"
    ")\n"
)


def main() -> None:
    if not CLI_PATH.exists():
        raise RuntimeError(
            "Run this script from the repository root; "
            "app/cio/cli.py was not found."
        )

    original = CLI_PATH.read_text(encoding="utf-8")
    text = original

    if IMPORT_BLOCK not in text:
        anchor_candidates = [
            "from app.cio.portfolio_simulation_service import (\n",
            "from app.cio.storage import Stage3Store\n",
        ]

        inserted = False
        for anchor in anchor_candidates:
            index = text.find(anchor)
            if index >= 0:
                text = text[:index] + IMPORT_BLOCK + text[index:]
                inserted = True
                break

        if not inserted:
            raise RuntimeError(
                "Could not locate a safe CLI import anchor. "
                "No changes were written."
            )

    fn_start = text.find("def opportunity_simulate(")
    if fn_start < 0:
        raise RuntimeError(
            "Could not find opportunity_simulate(). "
            "No changes were written."
        )

    next_fn = text.find("\ndef ", fn_start + 1)
    if next_fn < 0:
        next_fn = len(text)

    before = text[:fn_start]
    function = text[fn_start:next_fn]
    after = text[next_fn:]

    canonical_call = (
        "        build_canonical_portfolio_simulation_service(\n"
        "            store\n"
        "        )\n"
    )

    if "build_canonical_portfolio_simulation_service(" not in function:
        legacy_call = (
            "        PortfolioSimulationService(\n"
            "            store\n"
            "        )\n"
        )

        if legacy_call not in function:
            raise RuntimeError(
                "Could not find legacy PortfolioSimulationService(store) "
                "inside opportunity_simulate(). No changes were written."
            )

        function = function.replace(
            legacy_call,
            canonical_call,
            1,
        )

    text = before + function + after

    if text == original:
        print("PF-1G.5 CLI wiring already appears to be applied.")
        return

    backup = CLI_PATH.with_suffix(".py.pf1g5.bak")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    CLI_PATH.write_text(text, encoding="utf-8")

    print("PF-1G.5 CLI canonical Simulator V2 wiring applied.")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
