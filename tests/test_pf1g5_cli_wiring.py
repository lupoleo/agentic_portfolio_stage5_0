from __future__ import annotations

from pathlib import Path
import runpy


def test_pf1g5_cli_patcher_replaces_only_simulate_service_builder(
    tmp_path,
    monkeypatch,
):
    cli = tmp_path / "app/cio/cli.py"
    cli.parent.mkdir(parents=True)

    cli.write_text(
        '''from app.cio.portfolio_simulation_service import (
    PortfolioSimulationService,
)
from app.cio.storage import Stage3Store


def another_function(store):
    return PortfolioSimulationService(
        store
    )


def opportunity_simulate(store, opportunity_id):
    service = (
        PortfolioSimulationService(
            store
        )
    )
    return service


def opportunity_simulation(store, opportunity_id):
    return None
''',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    script = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "apply_pf1g5_cli.py"
    )

    runpy.run_path(str(script), run_name="__main__")

    text = cli.read_text(encoding="utf-8")

    assert (
        "from app.cio.portfolio_simulation_factory import ("
        in text
    )
    assert (
        "build_canonical_portfolio_simulation_service("
        in text
    )

    assert (
        '''def another_function(store):
    return PortfolioSimulationService(
        store
    )
'''
        in text
    )

    before = text
    runpy.run_path(str(script), run_name="__main__")
    assert cli.read_text(encoding="utf-8") == before
