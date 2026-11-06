from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.cio.models import (
    CioDecision,
    CioDecisionStatus,
    CioDecisionType,
    DecisionEvidenceAssessment,
    DecisionEvidenceOutcome,
    DecisionEvidenceType,
    PortfolioSimulation,
    RequiredChangeType,
    RequiredDecisionChange,
    TradeProposal,
)


class CioDecisionEngine:
    """
    Deterministic CIO Decision Engine V1.

    Purpose
    -------
    Convert a persisted TradeProposal + PortfolioSimulation into an
    auditable CIO decision:

        ACCEPT
        MODIFY
        REJECT

    Design principles
    -----------------
    1. Hard risk-constraint failures are authoritative.
    2. Missing critical evidence must not silently become ACCEPT.
    3. Warnings are classified into structured evidence assessments.
    4. MODIFY must include explicit remediation.
    5. AI reasoning can later enrich the rationale, but it must not
       override deterministic hard-constraint outcomes.

    V1 policy
    ---------
    REJECT
        One or more known hard constraints failed.

    MODIFY
        No known hard constraint failed, but critical evidence is
        incomplete or the proposal requires a structural improvement
        before approval.

    ACCEPT
        Hard constraints passed and no critical evidence gap remains.

    Broker execution remains manual and is outside this engine.
    """

    def decide(
        self,
        *,
        proposal: TradeProposal,
        simulation: PortfolioSimulation,
    ) -> CioDecision:
        """
        Produce a deterministic preliminary CIO decision.
        """

        self._validate_consistency(
            proposal=proposal,
            simulation=simulation,
        )

        assessments: list[
            DecisionEvidenceAssessment
        ] = []

        required_changes: list[
            RequiredDecisionChange
        ] = []

        warnings: list[str] = list(
            simulation.warnings
        )

        # =====================================================
        # 1. Hard constraints
        # =====================================================

        if simulation.constraints_passed:

            assessments.append(
                DecisionEvidenceAssessment(
                    evidence_type=(
                        DecisionEvidenceType.HARD_CONSTRAINT
                    ),
                    code="PORTFOLIO_CONSTRAINTS",
                    outcome=(
                        DecisionEvidenceOutcome.PASS
                    ),
                    summary=(
                        "All currently enforceable portfolio constraints "
                        "passed in the persisted PortfolioSimulation."
                    ),
                    critical=True,
                    source_id=(
                        simulation.simulation_id
                    ),
                )
            )

        else:

            assessments.append(
                DecisionEvidenceAssessment(
                    evidence_type=(
                        DecisionEvidenceType.HARD_CONSTRAINT
                    ),
                    code="PORTFOLIO_CONSTRAINTS",
                    outcome=(
                        DecisionEvidenceOutcome.FAIL
                    ),
                    summary=(
                        "One or more hard portfolio constraints failed "
                        "in the persisted PortfolioSimulation."
                    ),
                    critical=True,
                    source_id=(
                        simulation.simulation_id
                    ),
                )
            )

        for violation in (
            simulation.violated_constraints
        ):

            assessments.append(
                DecisionEvidenceAssessment(
                    evidence_type=(
                        DecisionEvidenceType.HARD_CONSTRAINT
                    ),
                    code=(
                        self._warning_code(
                            violation,
                            prefix="CONSTRAINT",
                        )
                    ),
                    outcome=(
                        DecisionEvidenceOutcome.FAIL
                    ),
                    summary=violation,
                    critical=True,
                    source_id=(
                        simulation.simulation_id
                    ),
                )
            )

        if not simulation.constraints_passed:

            return CioDecision(
                decision_id=(
                    self._new_decision_id(
                        proposal.ticker
                    )
                ),
                proposal_id=(
                    proposal.proposal_id
                ),
                simulation_id=(
                    simulation.simulation_id
                ),
                opportunity_id=(
                    proposal.opportunity_id
                ),
                snapshot_id=(
                    proposal.snapshot_id
                ),
                created_at=(
                    datetime.now(
                        timezone.utc
                    )
                ),
                decision=(
                    CioDecisionType.REJECT
                ),
                confidence=1.0,
                rationale=(
                    "The trade proposal is rejected because one or more "
                    "known hard portfolio constraints failed. "
                    "A deterministic risk failure cannot be overridden "
                    "by discretionary or AI reasoning."
                ),
                assessments=assessments,
                required_changes=[],
                warnings=warnings,
                hard_constraints_passed=False,
                critical_evidence_complete=True,
                status=(
                    CioDecisionStatus.PRELIMINARY
                ),
            )

        # =====================================================
        # 2. Trade-loss evidence
        # =====================================================

        if (
            proposal.estimated_max_loss_eur
            is None
        ):

            assessments.append(
                DecisionEvidenceAssessment(
                    evidence_type=(
                        DecisionEvidenceType.TRADE_STRUCTURE
                    ),
                    code="MAX_LOSS_UNKNOWN",
                    outcome=(
                        DecisionEvidenceOutcome.UNKNOWN
                    ),
                    summary=(
                        "Maximum intended trade loss cannot be "
                        "quantified from the current proposal."
                    ),
                    critical=True,
                    source_id=(
                        proposal.proposal_id
                    ),
                )
            )

            required_changes.append(
                RequiredDecisionChange(
                    change_type=(
                        RequiredChangeType.ADD_STOP
                    ),
                    description=(
                        "Define a stop / invalidation price so the "
                        "maximum trade loss can be calculated and "
                        "validated against the CIO risk budget."
                    ),
                    mandatory=True,
                )
            )

        else:

            assessments.append(
                DecisionEvidenceAssessment(
                    evidence_type=(
                        DecisionEvidenceType.TRADE_STRUCTURE
                    ),
                    code="MAX_LOSS_KNOWN",
                    outcome=(
                        DecisionEvidenceOutcome.PASS
                    ),
                    summary=(
                        "The proposal contains a quantified "
                        "estimated maximum loss."
                    ),
                    critical=True,
                    source_id=(
                        proposal.proposal_id
                    ),
                )
            )

        # =====================================================
        # 3. Analytical limitations from simulator V1
        # =====================================================

        for warning in simulation.warnings:

            lower = warning.lower()

            # -------------------------------------------------
            # Deduplicate trade-loss evidence.
            #
            # When estimated_max_loss_eur is unknown, section 2
            # has already created the canonical structured
            # MAX_LOSS_UNKNOWN assessment and ADD_STOP remediation.
            # Keep the raw simulator warning in decision.warnings,
            # but do not create a second evidence assessment for
            # the same underlying issue.
            # -------------------------------------------------

            if (
                proposal.estimated_max_loss_eur is None
                and (
                    "trade loss" in lower
                    or "estimated_max_loss_eur" in lower
                )
            ):
                continue

            # -------------------------------------------------
            # Missing canonical NAV/equity is a critical
            # evidence gap and has an explicit remediation.
            # -------------------------------------------------

            if (
                "gross exposure %" in lower
                and (
                    "nav" in lower
                    or "equity" in lower
                )
                and (
                    "cannot yet be evaluated" in lower
                    or "not available" in lower
                )
            ):

                assessments.append(
                    DecisionEvidenceAssessment(
                        evidence_type=(
                            DecisionEvidenceType.ANALYTICAL_WARNING
                        ),
                        code="PORTFOLIO_NAV_UNKNOWN",
                        outcome=(
                            DecisionEvidenceOutcome.UNKNOWN
                        ),
                        summary=warning,
                        critical=True,
                        source_id=(
                            simulation.simulation_id
                        ),
                    )
                )

                if not any(
                    change.change_type
                    == RequiredChangeType.PROVIDE_PORTFOLIO_NAV
                    for change in required_changes
                ):

                    required_changes.append(
                        RequiredDecisionChange(
                            change_type=(
                                RequiredChangeType.PROVIDE_PORTFOLIO_NAV
                            ),
                            description=(
                                "Provide a canonical portfolio NAV / "
                                "account-equity value so maximum gross "
                                "exposure as a percentage of portfolio "
                                "equity can be evaluated."
                            ),
                            mandatory=True,
                        )
                    )

                continue

            if (
                "not yet recomputed"
                in lower
            ):

                assessments.append(
                    DecisionEvidenceAssessment(
                        evidence_type=(
                            DecisionEvidenceType.ANALYTICAL_WARNING
                        ),
                        code=(
                            self._warning_code(
                                warning,
                                prefix="RECOMPUTE",
                            )
                        ),
                        outcome=(
                            DecisionEvidenceOutcome.WARNING
                        ),
                        summary=warning,
                        critical=False,
                        source_id=(
                            simulation.simulation_id
                        ),
                    )
                )

            elif (
                "cannot yet be evaluated"
                in lower
                or "unknown"
                in lower
            ):

                assessments.append(
                    DecisionEvidenceAssessment(
                        evidence_type=(
                            DecisionEvidenceType.ANALYTICAL_WARNING
                        ),
                        code=(
                            self._warning_code(
                                warning,
                                prefix="UNKNOWN",
                            )
                        ),
                        outcome=(
                            DecisionEvidenceOutcome.UNKNOWN
                        ),
                        summary=warning,
                        critical=(
                            self._is_critical_warning(
                                warning
                            )
                        ),
                        source_id=(
                            simulation.simulation_id
                        ),
                    )
                )

            else:

                assessments.append(
                    DecisionEvidenceAssessment(
                        evidence_type=(
                            DecisionEvidenceType.ANALYTICAL_WARNING
                        ),
                        code=(
                            self._warning_code(
                                warning,
                                prefix="WARNING",
                            )
                        ),
                        outcome=(
                            DecisionEvidenceOutcome.WARNING
                        ),
                        summary=warning,
                        critical=False,
                        source_id=(
                            simulation.simulation_id
                        ),
                    )
                )

        # =====================================================
        # 4. Portfolio fit
        # =====================================================

        assessments.append(
            DecisionEvidenceAssessment(
                evidence_type=(
                    DecisionEvidenceType.PORTFOLIO_FIT
                ),
                code="EXPOSURE_DIRECTION",
                outcome=(
                    DecisionEvidenceOutcome.PASS
                ),
                summary=(
                    self._portfolio_fit_summary(
                        simulation
                    )
                ),
                critical=False,
                source_id=(
                    simulation.simulation_id
                ),
            )
        )

        # =====================================================
        # 5. Determine critical evidence completeness
        # =====================================================

        critical_unknowns = [
            item
            for item in assessments
            if (
                item.critical
                and item.outcome
                in {
                    DecisionEvidenceOutcome.UNKNOWN,
                    DecisionEvidenceOutcome.WARNING,
                }
            )
        ]

        critical_evidence_complete = (
            len(
                critical_unknowns
            )
            == 0
        )

        # =====================================================
        # 6. Decision policy
        # =====================================================

        if required_changes or not critical_evidence_complete:

            rationale_parts = [
                (
                    "The proposal passes all currently enforceable "
                    "hard constraints, but it is not ready for approval "
                    "because critical evidence is incomplete."
                )
            ]

            if (
                proposal.estimated_max_loss_eur
                is None
            ):

                rationale_parts.append(
                    "The current proposal has no quantified maximum "
                    "loss because no stop / invalidation price is defined."
                )

            rationale_parts.append(
                "The CIO should modify the proposal and rerun the "
                "simulation before considering execution."
            )

            return CioDecision(
                decision_id=(
                    self._new_decision_id(
                        proposal.ticker
                    )
                ),
                proposal_id=(
                    proposal.proposal_id
                ),
                simulation_id=(
                    simulation.simulation_id
                ),
                opportunity_id=(
                    proposal.opportunity_id
                ),
                snapshot_id=(
                    proposal.snapshot_id
                ),
                created_at=(
                    datetime.now(
                        timezone.utc
                    )
                ),
                decision=(
                    CioDecisionType.MODIFY
                ),
                confidence=0.90,
                rationale=" ".join(
                    rationale_parts
                ),
                assessments=assessments,
                required_changes=(
                    required_changes
                    or [
                        RequiredDecisionChange(
                            change_type=(
                                RequiredChangeType.REFRESH_DATA
                            ),
                            description=(
                                "Resolve the outstanding critical "
                                "evidence gaps and rerun the CIO "
                                "decision pipeline."
                            ),
                            mandatory=True,
                        )
                    ]
                ),
                warnings=warnings,
                hard_constraints_passed=True,
                critical_evidence_complete=False,
                status=(
                    CioDecisionStatus.PRELIMINARY
                ),
            )

        # =====================================================
        # 7. ACCEPT
        # =====================================================

        return CioDecision(
            decision_id=(
                self._new_decision_id(
                    proposal.ticker
                )
            ),
            proposal_id=(
                proposal.proposal_id
            ),
            simulation_id=(
                simulation.simulation_id
            ),
            opportunity_id=(
                proposal.opportunity_id
            ),
            snapshot_id=(
                proposal.snapshot_id
            ),
            created_at=(
                datetime.now(
                    timezone.utc
                )
            ),
            decision=(
                CioDecisionType.ACCEPT
            ),
            confidence=0.85,
            rationale=(
                "The proposal passes all currently enforceable hard "
                "constraints and no critical evidence gap remains. "
                "The trade may proceed to human operator review. "
                "Broker execution is still manual."
            ),
            assessments=assessments,
            required_changes=[],
            warnings=warnings,
            hard_constraints_passed=True,
            critical_evidence_complete=True,
            status=(
                CioDecisionStatus.PRELIMINARY
            ),
        )

    # =========================================================
    # Validation
    # =========================================================

    @staticmethod
    def _validate_consistency(
        *,
        proposal: TradeProposal,
        simulation: PortfolioSimulation,
    ) -> None:

        if (
            simulation.proposal_id
            != proposal.proposal_id
        ):
            raise ValueError(
                "PortfolioSimulation does not belong to the supplied "
                "TradeProposal"
            )

        if (
            simulation.snapshot_id
            != proposal.snapshot_id
        ):
            raise ValueError(
                "PortfolioSimulation snapshot_id does not match the "
                "TradeProposal snapshot_id"
            )

    # =========================================================
    # Helpers
    # =========================================================

    @staticmethod
    def _new_decision_id(
        ticker: str,
    ) -> str:

        now = datetime.now(
            timezone.utc
        )

        return (
            f"DEC-{ticker.upper()}-"
            f"{now:%Y%m%d-%H%M%S}-"
            f"{uuid4().hex[:6]}"
        )

    @staticmethod
    def _warning_code(
        text: str,
        *,
        prefix: str,
    ) -> str:

        normalized = "".join(
            character
            if character.isalnum()
            else "_"
            for character in text.upper()
        )

        while "__" in normalized:
            normalized = normalized.replace(
                "__",
                "_",
            )

        normalized = (
            normalized.strip("_")
        )

        # Keep persisted decision payloads readable.
        normalized = normalized[:72]

        return (
            f"{prefix}_{normalized}"
        )

    @staticmethod
    def _is_critical_warning(
        warning: str,
    ) -> bool:

        lower = warning.lower()

        critical_terms = (
            "trade loss",
            "estimated_max_loss",
            "cash reserve",
            "gross exposure %",
            "cannot be fully evaluated",
        )

        return any(
            term in lower
            for term in critical_terms
        )

    @staticmethod
    def _portfolio_fit_summary(
        simulation: PortfolioSimulation,
    ) -> str:

        gross_delta = (
            simulation.delta.gross_exposure_eur
        )

        net_delta = (
            simulation.delta.net_exposure_eur
        )

        return (
            "Hypothetical trade changes gross exposure by "
            f"EUR {gross_delta:+,.2f} and net exposure by "
            f"EUR {net_delta:+,.2f}. "
            "Directional exposure changes are consistent with the "
            "persisted PortfolioSimulation."
        )