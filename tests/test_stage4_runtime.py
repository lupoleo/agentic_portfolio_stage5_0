from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.e2e.stage4_contracts import Stage4E2ERequest, Stage4StageStatus
from app.e2e.stage4_runtime import (
    CanonicalStage4Runtime,
    Stage4RuntimeConfiguration,
)


NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)


def configuration(**updates):
    values = dict(
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        eligibility_report_path="eligibility.json",
        mapping_report_path="mapping.json",
        history_report_paths=("history.json",),
    )
    values.update(updates)
    return Stage4RuntimeConfiguration(**values)


def request():
    return Stage4E2ERequest(
        portfolio_file="portfolio.xlsx",
        portfolio_file_fingerprint="portfolio-fp",
        account_state_id="account-1",
        scanner_configuration_id="scanner-v1",
        scanner_configuration_fingerprint="scanner-fp",
        as_of=NOW,
    )


def runtime_with_store(store):
    value = object.__new__(CanonicalStage4Runtime)
    value.configuration = configuration()
    value.stage3_store = store
    return value


def test_runtime_configuration_requires_explicit_history_reports():
    with pytest.raises(ValidationError, match="history report"):
        configuration(history_report_paths=())


def test_runtime_configuration_fingerprint_is_deterministic():
    assert configuration().fingerprint == configuration().fingerprint
    assert configuration(model_name="another").fingerprint != configuration().fingerprint


def test_portfolio_adapter_accepts_only_explicit_matching_lineage():
    store = SimpleNamespace(
        get_portfolio_snapshot=lambda value: SimpleNamespace(
            snapshot_id=value,
            source_file_hash="portfolio-fp",
            account_state_id="account-1",
        ),
        get_portfolio_risk_state=lambda value: SimpleNamespace(
            risk_state_id=value,
            snapshot_id="snapshot-1",
        ),
        get_account_state=lambda value: SimpleNamespace(account_state_id=value),
    )
    result = runtime_with_store(store).portfolio_analysis(request(), None)
    assert result.status is Stage4StageStatus.COMPLETED
    assert result.output_payload["portfolio_snapshot_id"] == "snapshot-1"


def test_portfolio_adapter_blocks_snapshot_file_mismatch():
    store = SimpleNamespace(
        get_portfolio_snapshot=lambda value: SimpleNamespace(
            snapshot_id=value,
            source_file_hash="wrong",
            account_state_id="account-1",
        ),
        get_portfolio_risk_state=lambda value: SimpleNamespace(
            risk_state_id=value,
            snapshot_id="snapshot-1",
        ),
        get_account_state=lambda value: SimpleNamespace(account_state_id=value),
    )
    result = runtime_with_store(store).portfolio_analysis(request(), None)
    assert result.status is Stage4StageStatus.BLOCKED
