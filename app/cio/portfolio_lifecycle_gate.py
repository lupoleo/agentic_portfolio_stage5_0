from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.cio.models import PortfolioFitDecision
from app.cio.storage import Stage3Store


class PortfolioLifecycleAction(str, Enum):
    """
    Deterministic workflow consequence of a PortfolioFitAssessment.

    This is intentionally distinct from PortfolioFitDecision:
    the former is a lifecycle action, the latter is an analytical result.
    """

    ADVANCE = "ADVANCE"
    ADVANCE_WITH_WARNING = "ADVANCE_WITH_WARNING"
    BLOCK = "BLOCK"


class PortfolioLifecycleGateReason(str, Enum):
    """
    Machine-readable reason for the lifecycle gate result.
    """

    PORTFOLIO_FIT_PASS = "PORTFOLIO_FIT_PASS"
    PORTFOLIO_FIT_WARNING = "PORTFOLIO_FIT_WARNING"
    PORTFOLIO_FIT_REJECT = "PORTFOLIO_FIT_REJECT"
    PORTFOLIO_FIT_UNKNOWN = "PORTFOLIO_FIT_UNKNOWN"
    STALE_PORTFOLIO_STATE = "STALE_PORTFOLIO_STATE"


class PortfolioLifecycleGateResult(BaseModel):
    """
    Derived, non-persisted V1 lifecycle gate result.

    The gate does not approve execution.  ADVANCE only means that the
    supplied persisted PortfolioFitAssessment permits the opportunity to
    proceed to Instrument Selection.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
    )

    opportunity_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)

    portfolio_fit_decision: PortfolioFitDecision
    action: PortfolioLifecycleAction
    can_advance: bool

    reason_code: PortfolioLifecycleGateReason
    reason: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_consistency(self) -> "PortfolioLifecycleGateResult":
        if self.action in {
            PortfolioLifecycleAction.ADVANCE,
            PortfolioLifecycleAction.ADVANCE_WITH_WARNING,
        }:
            if not self.can_advance:
                raise ValueError(
                    "ADVANCE actions require can_advance=True"
                )

        if (
            self.action == PortfolioLifecycleAction.BLOCK
            and self.can_advance
        ):
            raise ValueError(
                "BLOCK requires can_advance=False"
            )

        return self


class PortfolioLifecycleGate:
    """
    Deterministic V1 gate between Portfolio Filter and Instrument Selection.

    V1 execution model:
        - one TradeOpportunity is advanced at a time;
        - the gate consumes one explicit persisted assessment_id;
        - PASS and PASS_WITH_WARNING may advance;
        - REJECT and UNKNOWN fail closed;
        - an opportunity tied to a non-current PortfolioSnapshot is blocked.

    A stale snapshot is a valid lifecycle condition, not a data-integrity
    exception.  Missing/mismatched persisted provenance is treated as an
    error because it indicates invalid lifecycle state.
    """

    def __init__(self, store: Stage3Store) -> None:
        self.store = store

    def evaluate(
        self,
        assessment_id: str,
    ) -> PortfolioLifecycleGateResult:
        assessment = self.store.get_portfolio_fit_assessment(
            assessment_id
        )

        if assessment is None:
            raise ValueError(
                "PortfolioFitAssessment not found: "
                f"{assessment_id}"
            )

        opportunity = self.store.get_trade_opportunity(
            assessment.opportunity_id
        )

        if opportunity is None:
            raise ValueError(
                "TradeOpportunity not found: "
                f"{assessment.opportunity_id}"
            )

        if (
            opportunity.opportunity_id
            != assessment.opportunity_id
        ):
            raise ValueError(
                "PortfolioFitAssessment opportunity_id does not "
                "match the TradeOpportunity"
            )

        if (
            opportunity.snapshot_id
            != assessment.snapshot_id
        ):
            raise ValueError(
                "PortfolioFitAssessment snapshot_id does not match "
                "the TradeOpportunity snapshot_id"
            )

        if opportunity.ticker.upper() != assessment.ticker.upper():
            raise ValueError(
                "PortfolioFitAssessment ticker does not match "
                "the TradeOpportunity"
            )

        if opportunity.direction != assessment.direction:
            raise ValueError(
                "PortfolioFitAssessment direction does not match "
                "the TradeOpportunity"
            )

        snapshot = self.store.get_portfolio_snapshot(
            assessment.snapshot_id
        )

        if snapshot is None:
            raise ValueError(
                "PortfolioSnapshot not found: "
                f"{assessment.snapshot_id}"
            )

        latest_snapshot = (
            self.store.get_latest_portfolio_snapshot()
        )

        if latest_snapshot is None:
            raise ValueError(
                "No current PortfolioSnapshot is available"
            )

        # -----------------------------------------------------
        # V1 one-by-one safety rule.
        #
        # Once a successful execution changes the portfolio and a new
        # analysis creates a newer snapshot, older opportunities remain
        # auditable but cannot silently continue through the lifecycle.
        # -----------------------------------------------------

        if (
            assessment.snapshot_id
            != latest_snapshot.snapshot_id
        ):
            return PortfolioLifecycleGateResult(
                opportunity_id=assessment.opportunity_id,
                assessment_id=assessment.assessment_id,
                snapshot_id=assessment.snapshot_id,
                portfolio_fit_decision=assessment.decision,
                action=PortfolioLifecycleAction.BLOCK,
                can_advance=False,
                reason_code=(
                    PortfolioLifecycleGateReason
                    .STALE_PORTFOLIO_STATE
                ),
                reason=(
                    "Opportunity belongs to a stale portfolio "
                    "snapshot and must be reassessed against the "
                    "current portfolio state."
                ),
                warnings=list(assessment.warnings),
            )

        if assessment.decision == PortfolioFitDecision.PASS:
            return PortfolioLifecycleGateResult(
                opportunity_id=assessment.opportunity_id,
                assessment_id=assessment.assessment_id,
                snapshot_id=assessment.snapshot_id,
                portfolio_fit_decision=assessment.decision,
                action=PortfolioLifecycleAction.ADVANCE,
                can_advance=True,
                reason_code=(
                    PortfolioLifecycleGateReason
                    .PORTFOLIO_FIT_PASS
                ),
                reason=(
                    "Portfolio fit assessment passed; opportunity "
                    "is eligible for Instrument Selection."
                ),
                warnings=list(assessment.warnings),
            )

        if (
            assessment.decision
            == PortfolioFitDecision.PASS_WITH_WARNING
        ):
            return PortfolioLifecycleGateResult(
                opportunity_id=assessment.opportunity_id,
                assessment_id=assessment.assessment_id,
                snapshot_id=assessment.snapshot_id,
                portfolio_fit_decision=assessment.decision,
                action=(
                    PortfolioLifecycleAction
                    .ADVANCE_WITH_WARNING
                ),
                can_advance=True,
                reason_code=(
                    PortfolioLifecycleGateReason
                    .PORTFOLIO_FIT_WARNING
                ),
                reason=(
                    "Portfolio fit assessment permits lifecycle "
                    "advancement with warnings."
                ),
                warnings=list(assessment.warnings),
            )

        if assessment.decision == PortfolioFitDecision.REJECT:
            return PortfolioLifecycleGateResult(
                opportunity_id=assessment.opportunity_id,
                assessment_id=assessment.assessment_id,
                snapshot_id=assessment.snapshot_id,
                portfolio_fit_decision=assessment.decision,
                action=PortfolioLifecycleAction.BLOCK,
                can_advance=False,
                reason_code=(
                    PortfolioLifecycleGateReason
                    .PORTFOLIO_FIT_REJECT
                ),
                reason=(
                    "Portfolio fit assessment rejected the "
                    "opportunity."
                ),
                warnings=list(assessment.warnings),
            )

        if assessment.decision == PortfolioFitDecision.UNKNOWN:
            return PortfolioLifecycleGateResult(
                opportunity_id=assessment.opportunity_id,
                assessment_id=assessment.assessment_id,
                snapshot_id=assessment.snapshot_id,
                portfolio_fit_decision=assessment.decision,
                action=PortfolioLifecycleAction.BLOCK,
                can_advance=False,
                reason_code=(
                    PortfolioLifecycleGateReason
                    .PORTFOLIO_FIT_UNKNOWN
                ),
                reason=(
                    "Portfolio fit assessment is unresolved; "
                    "lifecycle fails closed."
                ),
                warnings=list(assessment.warnings),
            )

        raise ValueError(
            "Unsupported PortfolioFitDecision: "
            f"{assessment.decision}"
        )
