from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from app.e2e.stage4_replenishment_runtime import (
    CanonicalStage4CandidateWaveExecutor,
    Stage4ReplenishmentRuntimeConfiguration,
)


NOW = datetime(2026, 9, 27, 20, 10, tzinfo=timezone.utc)


def configuration(tmp_path):
    eligibility = tmp_path / "eligibility.json"
    eligibility.write_text(
        json.dumps({"all_decisions": []}),
        encoding="utf-8",
    )
    return Stage4ReplenishmentRuntimeConfiguration(
        database_path=str(tmp_path / "state.db"),
        portfolio_file=str(tmp_path / "portfolio.xlsx"),
        account_state_id="account-1",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        eligibility_report_path=str(eligibility),
        mapping_report_path=str(tmp_path / "mapping.json"),
        history_output_directory=str(tmp_path / "history"),
        child_output_directory=str(tmp_path / "children"),
        pause_seconds=0,
    )


def plan():
    return SimpleNamespace(
        selected_listings=(
            SimpleNamespace(
                exchange="BIT",
                listing_key="BIT:AMP",
            ),
            SimpleNamespace(
                exchange="BIT",
                listing_key="BIT:AVIO",
            ),
        )
    )


def test_history_verification_uses_exact_immutable_wave_clock(
    tmp_path, monkeypatch,
):
    captured = {}

    class CapturingHistoryProvider:
        def __init__(self, *, timeout_seconds, now):
            self.now = now
            captured["timeout_seconds"] = timeout_seconds
            captured["now"] = now

    def fake_run_pilot(data, **kwargs):
        provider = kwargs["history_provider"]
        assert provider.now() == NOW
        output = Path(kwargs["output"])
        output.mkdir(parents=True, exist_ok=True)
        path = output / (
            "scanner_history_quality_"
            + NOW.strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
        path.write_text(
            json.dumps({
                "run_status": "COMPLETED",
                "results": [],
            }),
            encoding="utf-8",
        )
        return 2

    monkeypatch.setattr(
        "app.e2e.stage4_replenishment_runtime."
        "YahooHistorySnapshotProvider",
        CapturingHistoryProvider,
    )
    monkeypatch.setattr(
        "app.e2e.stage4_replenishment_runtime.run_pilot",
        fake_run_pilot,
    )

    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path)
    )
    path = executor._run_history(plan(), NOW)

    assert path.is_file()
    assert captured["now"]() == NOW
    assert captured["timeout_seconds"] == 20.0


def test_dynamic_system_clock_cannot_leak_into_wave_evidence(
    tmp_path, monkeypatch,
):
    observed = []

    class CapturingHistoryProvider:
        def __init__(self, *, timeout_seconds, now):
            observed.extend((now(), now()))

    def fake_run_pilot(data, **kwargs):
        output = Path(kwargs["output"])
        output.mkdir(parents=True, exist_ok=True)
        path = output / (
            "scanner_history_quality_"
            + NOW.strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
        path.write_text(
            '{"run_status":"COMPLETED","results":[]}',
            encoding="utf-8",
        )
        return 2

    monkeypatch.setattr(
        "app.e2e.stage4_replenishment_runtime."
        "YahooHistorySnapshotProvider",
        CapturingHistoryProvider,
    )
    monkeypatch.setattr(
        "app.e2e.stage4_replenishment_runtime.run_pilot",
        fake_run_pilot,
    )

    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path)
    )
    executor._run_history(plan(), NOW)
    assert observed == [NOW, NOW]
