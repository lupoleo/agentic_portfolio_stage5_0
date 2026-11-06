from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunMode,
    SelectedOpportunityDryRunRequest,
    SelectedOpportunityParameters,
)


NOW = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)


def parameters(**changes):
    values = dict(
        requested_exposure_eur=10_000,
        max_intended_loss_eur=250,
        reference_price=100,
        fx_to_eur=0.85,
        stop_price=95,
        market_observed_at=NOW - timedelta(minutes=5),
    )
    values.update(changes)
    return SelectedOpportunityParameters(**values)


def request(**changes):
    values = dict(
        scanner_research_run_id="s2f-run",
        opportunity_ids=("opp-1",),
        portfolio_snapshot_id="snapshot-1",
        as_of=NOW,
        mode=DryRunMode.LIVE,
        parameters=parameters(),
    )
    values.update(changes)
    return SelectedOpportunityDryRunRequest(**values)


def test_request_preserves_exactly_one_selected_identity():
    value = request(opportunity_ids=(" opp-1 ", "opp-1"))
    assert value.opportunity_ids == ("opp-1",)
    assert value.opportunity_id == "opp-1"


def test_multiple_selections_are_rejected_before_orchestration():
    with pytest.raises(ValidationError, match="at most one"):
        request(opportunity_ids=("opp-1", "opp-2"))


def test_zero_selection_is_representable_for_real_data_replay():
    value = request(opportunity_ids=(), parameters=None)
    assert value.opportunity_id is None


def test_future_market_input_is_rejected():
    with pytest.raises(ValidationError, match="newer than"):
        request(parameters=parameters(market_observed_at=NOW + timedelta(seconds=1)))


def test_dry_run_policy_cannot_enable_execution():
    from app.scanner.selected_opportunity_dry_run_contracts import (
        SelectedOpportunityDryRunPolicy,
    )

    with pytest.raises(ValidationError, match="cannot enable"):
        SelectedOpportunityDryRunPolicy(broker_execution_enabled=True)


@pytest.mark.parametrize(
    ("override", "message"),
    (
        ({"require_current_snapshot": False}, "current portfolio snapshot"),
        ({"require_s2f_provenance": False}, "persisted S2.2F provenance"),
    ),
)
def test_dry_run_policy_cannot_disable_fail_closed_guards(override, message):
    from app.scanner.selected_opportunity_dry_run_contracts import (
        SelectedOpportunityDryRunPolicy,
    )

    with pytest.raises(ValidationError, match=message):
        SelectedOpportunityDryRunPolicy(**override)
