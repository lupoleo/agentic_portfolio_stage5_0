from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.cio.models import Direction, PortfolioFitDecision
from app.cio.portfolio_lifecycle_gate import (
    PortfolioLifecycleAction,
    PortfolioLifecycleGate,
    PortfolioLifecycleGateReason,
)


class FakeStore:
    def __init__(
        self,
        *,
        assessment,
        opportunity,
        snapshot,
        latest_snapshot,
    ):
        self.assessment = assessment
        self.opportunity = opportunity
        self.snapshot = snapshot
        self.latest_snapshot = latest_snapshot

    def get_portfolio_fit_assessment(self, assessment_id):
        if (
            self.assessment is not None
            and self.assessment.assessment_id == assessment_id
        ):
            return self.assessment
        return None

    def get_trade_opportunity(self, opportunity_id):
        if (
            self.opportunity is not None
            and self.opportunity.opportunity_id == opportunity_id
        ):
            return self.opportunity
        return None

    def get_portfolio_snapshot(self, snapshot_id):
        if (
            self.snapshot is not None
            and self.snapshot.snapshot_id == snapshot_id
        ):
            return self.snapshot
        return None

    def get_latest_portfolio_snapshot(self):
        return self.latest_snapshot


def _assessment(
    decision: PortfolioFitDecision,
    *,
    warnings=None,
    opportunity_id="OPP-1",
    snapshot_id="SNAP-1",
    ticker="QCOM",
    direction=Direction.LONG,
):
    return SimpleNamespace(
        assessment_id="PFIT-1",
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        ticker=ticker,
        direction=direction,
        decision=decision,
        warnings=list(warnings or []),
    )


def _opportunity(
    *,
    opportunity_id="OPP-1",
    snapshot_id="SNAP-1",
    ticker="QCOM",
    direction=Direction.LONG,
):
    return SimpleNamespace(
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        ticker=ticker,
        direction=direction,
    )


def _snapshot(snapshot_id="SNAP-1"):
    return SimpleNamespace(snapshot_id=snapshot_id)


def _gate(
    decision: PortfolioFitDecision,
    *,
    warnings=None,
    assessment=None,
    opportunity=None,
    snapshot=None,
    latest_snapshot=None,
):
    assessment = (
        assessment
        if assessment is not None
        else _assessment(decision, warnings=warnings)
    )
    opportunity = (
        opportunity
        if opportunity is not None
        else _opportunity()
    )
    snapshot = (
        snapshot
        if snapshot is not None
        else _snapshot()
    )
    latest_snapshot = (
        latest_snapshot
        if latest_snapshot is not None
        else _snapshot()
    )

    return PortfolioLifecycleGate(
        FakeStore(
            assessment=assessment,
            opportunity=opportunity,
            snapshot=snapshot,
            latest_snapshot=latest_snapshot,
        )
    )


def test_pass_advances():
    result = _gate(
        PortfolioFitDecision.PASS
    ).evaluate("PFIT-1")

    assert result.action == PortfolioLifecycleAction.ADVANCE
    assert result.can_advance is True
    assert (
        result.reason_code
        == PortfolioLifecycleGateReason.PORTFOLIO_FIT_PASS
    )


def test_pass_with_warning_advances_and_preserves_warnings():
    warnings = [
        "Configured constraint remains UNKNOWN.",
        "Analytical coverage is limited.",
    ]

    result = _gate(
        PortfolioFitDecision.PASS_WITH_WARNING,
        warnings=warnings,
    ).evaluate("PFIT-1")

    assert (
        result.action
        == PortfolioLifecycleAction.ADVANCE_WITH_WARNING
    )
    assert result.can_advance is True
    assert result.warnings == warnings
    assert (
        result.reason_code
        == PortfolioLifecycleGateReason.PORTFOLIO_FIT_WARNING
    )


def test_reject_blocks():
    result = _gate(
        PortfolioFitDecision.REJECT
    ).evaluate("PFIT-1")

    assert result.action == PortfolioLifecycleAction.BLOCK
    assert result.can_advance is False
    assert (
        result.reason_code
        == PortfolioLifecycleGateReason.PORTFOLIO_FIT_REJECT
    )


def test_unknown_fails_closed():
    result = _gate(
        PortfolioFitDecision.UNKNOWN
    ).evaluate("PFIT-1")

    assert result.action == PortfolioLifecycleAction.BLOCK
    assert result.can_advance is False
    assert (
        result.reason_code
        == PortfolioLifecycleGateReason.PORTFOLIO_FIT_UNKNOWN
    )


def test_stale_snapshot_blocks_even_when_portfolio_fit_passed():
    result = _gate(
        PortfolioFitDecision.PASS,
        latest_snapshot=_snapshot("SNAP-2"),
    ).evaluate("PFIT-1")

    assert result.action == PortfolioLifecycleAction.BLOCK
    assert result.can_advance is False
    assert (
        result.reason_code
        == PortfolioLifecycleGateReason.STALE_PORTFOLIO_STATE
    )


def test_stale_snapshot_has_precedence_over_pass_with_warning():
    result = _gate(
        PortfolioFitDecision.PASS_WITH_WARNING,
        warnings=["warning"],
        latest_snapshot=_snapshot("SNAP-2"),
    ).evaluate("PFIT-1")

    assert result.action == PortfolioLifecycleAction.BLOCK
    assert result.can_advance is False
    assert result.warnings == ["warning"]
    assert (
        result.reason_code
        == PortfolioLifecycleGateReason.STALE_PORTFOLIO_STATE
    )


def test_missing_assessment_is_error():
    store = FakeStore(
        assessment=None,
        opportunity=_opportunity(),
        snapshot=_snapshot(),
        latest_snapshot=_snapshot(),
    )
    gate = PortfolioLifecycleGate(store)

    with pytest.raises(
        ValueError,
        match="PortfolioFitAssessment not found",
    ):
        gate.evaluate("PFIT-MISSING")


def test_missing_opportunity_is_error():
    assessment = _assessment(
        PortfolioFitDecision.PASS
    )
    store = FakeStore(
        assessment=assessment,
        opportunity=None,
        snapshot=_snapshot(),
        latest_snapshot=_snapshot(),
    )
    gate = PortfolioLifecycleGate(store)

    with pytest.raises(
        ValueError,
        match="TradeOpportunity not found",
    ):
        gate.evaluate("PFIT-1")


def test_assessment_snapshot_mismatch_is_error():
    assessment = _assessment(
        PortfolioFitDecision.PASS,
        snapshot_id="SNAP-1",
    )
    opportunity = _opportunity(
        snapshot_id="SNAP-2",
    )

    gate = _gate(
        PortfolioFitDecision.PASS,
        assessment=assessment,
        opportunity=opportunity,
    )

    with pytest.raises(
        ValueError,
        match="snapshot_id does not match",
    ):
        gate.evaluate("PFIT-1")


def test_ticker_mismatch_is_error():
    assessment = _assessment(
        PortfolioFitDecision.PASS,
        ticker="QCOM",
    )
    opportunity = _opportunity(
        ticker="NVDA",
    )

    gate = _gate(
        PortfolioFitDecision.PASS,
        assessment=assessment,
        opportunity=opportunity,
    )

    with pytest.raises(
        ValueError,
        match="ticker does not match",
    ):
        gate.evaluate("PFIT-1")


def test_direction_mismatch_is_error():
    assessment = _assessment(
        PortfolioFitDecision.PASS,
        direction=Direction.LONG,
    )
    opportunity = _opportunity(
        direction=Direction.SHORT,
    )

    gate = _gate(
        PortfolioFitDecision.PASS,
        assessment=assessment,
        opportunity=opportunity,
    )

    with pytest.raises(
        ValueError,
        match="direction does not match",
    ):
        gate.evaluate("PFIT-1")


def test_missing_snapshot_is_error():
    assessment = _assessment(
        PortfolioFitDecision.PASS
    )
    store = FakeStore(
        assessment=assessment,
        opportunity=_opportunity(),
        snapshot=None,
        latest_snapshot=_snapshot(),
    )
    gate = PortfolioLifecycleGate(store)

    with pytest.raises(
        ValueError,
        match="PortfolioSnapshot not found",
    ):
        gate.evaluate("PFIT-1")


def test_missing_current_snapshot_is_error():
    assessment = _assessment(
        PortfolioFitDecision.PASS
    )
    store = FakeStore(
        assessment=assessment,
        opportunity=_opportunity(),
        snapshot=_snapshot(),
        latest_snapshot=None,
    )
    gate = PortfolioLifecycleGate(store)

    with pytest.raises(
        ValueError,
        match="No current PortfolioSnapshot",
    ):
        gate.evaluate("PFIT-1")
