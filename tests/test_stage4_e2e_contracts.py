from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.e2e.stage4_contracts import (
    Stage4E2EPolicy,
    Stage4E2ERequest,
    Stage4E2ERun,
    Stage4Mode,
    Stage4RunStatus,
    Stage4TerminalReason,
    canonical_fingerprint,
    stage4_request_fingerprint,
    stage4_run_id,
)


NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


def request(**updates):
    values = dict(
        portfolio_file="data/input/portafoglio-export.xlsx",
        portfolio_file_fingerprint="portfolio-fp",
        account_state_id="account-001",
        scanner_configuration_id="scanner-v1",
        scanner_configuration_fingerprint="scanner-fp",
        as_of=NOW,
    )
    values.update(updates)
    return Stage4E2ERequest(**values)


def test_request_is_immutable_and_timezone_aware():
    value = request()
    with pytest.raises(ValidationError):
        value.account_state_id = "changed"
    with pytest.raises(ValidationError, match="timezone-aware"):
        request(as_of=NOW.replace(tzinfo=None))


def test_request_accepts_zero_or_one_explicit_selection_only():
    assert request().selected_opportunity_id is None
    assert request(selected_opportunity_ids=("opp-1",)).selected_opportunity_id == "opp-1"
    with pytest.raises(ValidationError, match="at most one"):
        request(selected_opportunity_ids=("opp-1", "opp-2"))


def test_run_identity_is_independent_of_provider_mode():
    live = request(mode=Stage4Mode.LIVE)
    cached = request(mode=Stage4Mode.CACHE_ONLY)
    assert stage4_request_fingerprint(live) == stage4_request_fingerprint(cached)
    assert stage4_run_id(live) == stage4_run_id(cached)


@pytest.mark.parametrize(
    "override",
    [
        {"preserve_existing_contracts": False},
        {"require_explicit_operator_selection": False},
        {"allow_batch_allocation": True},
        {"broker_execution_enabled": True},
        {"portfolio_mutation_enabled": True},
    ],
)
def test_policy_cannot_weaken_frozen_contract_or_safety(override):
    with pytest.raises(ValidationError):
        Stage4E2EPolicy(**override)


def test_completed_run_requires_non_authorized_plan_and_zero_side_effects():
    value = request(selected_opportunity_ids=("opp-1",))
    base = dict(
        run_id=stage4_run_id(value),
        policy_id=value.policy.policy_id,
        policy_version=value.policy.policy_version,
        mode=value.mode,
        status=Stage4RunStatus.COMPLETED,
        terminal_reason=Stage4TerminalReason.E2E_DRY_RUN_COMPLETED,
        as_of=value.as_of,
        started_at=NOW,
        completed_at=NOW,
        portfolio_file_fingerprint=value.portfolio_file_fingerprint,
        account_state_id=value.account_state_id,
        selected_opportunity_id="opp-1",
        execution_plan_id="plan-1",
        request_fingerprint=stage4_request_fingerprint(value),
        fingerprint=canonical_fingerprint({"run": "complete"}),
    )
    result = Stage4E2ERun(**base)
    assert result.execution_authorized is False
    assert result.broker_orders_submitted == 0
    with pytest.raises(ValidationError, match="side effects"):
        Stage4E2ERun(**base, broker_orders_submitted=1)


def test_terminal_run_requires_completion_and_reason():
    value = request()
    with pytest.raises(ValidationError, match="completion and reason"):
        Stage4E2ERun(
            run_id=stage4_run_id(value),
            policy_id=value.policy.policy_id,
            policy_version=value.policy.policy_version,
            mode=value.mode,
            status=Stage4RunStatus.BLOCKED,
            as_of=NOW,
            started_at=NOW,
            portfolio_file_fingerprint=value.portfolio_file_fingerprint,
            account_state_id=value.account_state_id,
            request_fingerprint=stage4_request_fingerprint(value),
            fingerprint=canonical_fingerprint({"run": "blocked"}),
        )
