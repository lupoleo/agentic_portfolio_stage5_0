import json
from datetime import datetime, timezone
from pathlib import Path

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment import (
    create_replenishment_session,
    plan_candidate_wave,
)
from app.e2e.stage4_replenishment_runtime import (
    CanonicalStage4CandidateWaveExecutor,
    Stage4ReplenishmentRuntimeConfiguration,
)


NOW = datetime(2026, 9, 28, 10, 18, 31, tzinfo=timezone.utc)


def test_history_and_fx_providers_share_exact_wave_clock(
    tmp_path, monkeypatch,
):
    captured = {}

    class CapturingHistoryProvider:
        def __init__(self, *, timeout_seconds, now):
            captured["history_timeout"] = timeout_seconds
            captured["history_now"] = now
            self._now = now

        def now(self):
            return self._now()

    class CapturingFXProvider:
        def __init__(self, *, timeout_seconds, now):
            captured["fx_timeout"] = timeout_seconds
            captured["fx_now"] = now
            self._now = now

        def now(self):
            return self._now()

    def fake_run_pilot(data, **kwargs):
        assert kwargs["history_provider"].now() == NOW
        assert kwargs["fx_provider"].now() == NOW
        assert kwargs["now"]() == NOW
        output = Path(kwargs["output"])
        output.mkdir(parents=True, exist_ok=True)
        path = output / (
            "scanner_history_quality_"
            + NOW.strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
        path.write_text(
            json.dumps({"run_status": "COMPLETED", "results": []}),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(
        "app.e2e.stage4_replenishment_runtime."
        "YahooHistorySnapshotProvider",
        CapturingHistoryProvider,
    )
    monkeypatch.setattr(
        "app.e2e.stage4_replenishment_runtime.ECBSessionFXProvider",
        CapturingFXProvider,
    )
    monkeypatch.setattr(
        "app.e2e.stage4_replenishment_runtime.run_pilot",
        fake_run_pilot,
    )

    eligibility = {"all_decisions": [
        {"exchange": "NASDAQ", "symbol": "AAL", "status": "ELIGIBLE"},
    ]}
    mapping = {"mappings": [
        {
            "exchange": "NASDAQ",
            "symbol": "AAL",
            "mapping_status": "RESOLVED",
            "resolved_symbol": "AAL",
        },
    ]}
    eligibility_path = tmp_path / "eligibility.json"
    mapping_path = tmp_path / "mapping.json"
    eligibility_path.write_text(json.dumps(eligibility), encoding="utf-8")
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    configuration = Stage4ReplenishmentRuntimeConfiguration(
        database_path=str(tmp_path / "state.db"),
        portfolio_file=str(tmp_path / "portfolio.xlsx"),
        account_state_id="account-1",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        eligibility_report_path=str(eligibility_path),
        mapping_report_path=str(mapping_path),
        history_output_directory=str(tmp_path / "history"),
        child_output_directory=str(tmp_path / "children"),
    )
    session = create_replenishment_session(
        root_run_id="root-1",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=Stage4Mode.LIVE,
    )
    plan = plan_candidate_wave(
        session=session,
        wave_index=1,
        eligibility=eligibility,
        mapping=mapping,
        prior_outcomes=(),
        attempted_listing_keys=(),
    )
    path = CanonicalStage4CandidateWaveExecutor(
        configuration
    )._run_history(plan, NOW)

    assert path.is_file()
    assert captured["history_now"]() == NOW
    assert captured["fx_now"]() == NOW
    assert captured["history_timeout"] == captured["fx_timeout"]
