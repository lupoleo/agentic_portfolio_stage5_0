from __future__ import annotations

from dataclasses import dataclass

from app.cio.models import (
    Direction,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentCandidate,
    InstrumentType,
    TradeOpportunity,
    TradingHorizon,
    TradingMode,
)


@dataclass(frozen=True)
class SelectionDecision:
    eligible: bool
    rejection_reason: str | None
    suitability_score: float


class InstrumentSelector:
    """
    Deterministic Stage 3 Instrument Selector.

    Responsibilities:

        TradeOpportunity
            +
        FinecoInstrument cache
            ↓
        InstrumentCandidate[]

    The selector does NOT:
        - size the position;
        - calculate portfolio VaR;
        - generate the final TradeProposal;
        - invent unknown broker characteristics.

    It only determines whether an instrument can implement the
    opportunity and assigns a relative suitability score.
    """

    def select(
        self,
        opportunity: TradeOpportunity,
        instruments: list[FinecoInstrument],
    ) -> list[InstrumentCandidate]:

        candidates: list[InstrumentCandidate] = []

        for instrument in instruments:

            decision = self._evaluate(
                opportunity,
                instrument,
            )

            candidates.append(
                InstrumentCandidate(
                    instrument_id=(
                        instrument.instrument_id
                    ),
                    opportunity_id=(
                        opportunity.opportunity_id
                    ),
                    eligible=(
                        decision.eligible
                    ),
                    rejection_reason=(
                        decision.rejection_reason
                    ),
                    estimated_margin_eur=None,
                    estimated_cost_eur=None,
                    suitability_score=(
                        decision.suitability_score
                    ),
                )
            )

        # Eligible first, then highest score.
        return sorted(
            candidates,
            key=lambda item: (
                not item.eligible,
                -(
                    item.suitability_score
                    if item.suitability_score is not None
                    else 0.0
                ),
                item.instrument_id,
            ),
        )

    def _evaluate(
        self,
        opportunity: TradeOpportunity,
        instrument: FinecoInstrument,
    ) -> SelectionDecision:

        # -----------------------------------------------------
        # 1. Direction compatibility
        # -----------------------------------------------------

        direction_result = (
            self._direction_compatibility(
                opportunity,
                instrument,
            )
        )

        if direction_result is not None:
            return direction_result

        # -----------------------------------------------------
        # 2. Horizon compatibility
        # -----------------------------------------------------

        horizon_result = (
            self._horizon_compatibility(
                opportunity,
                instrument,
            )
        )

        if horizon_result is not None:
            return horizon_result

        # -----------------------------------------------------
        # 3. Base score
        # -----------------------------------------------------

        score = 0.50

        # -----------------------------------------------------
        # 4. Direction implementation quality
        # -----------------------------------------------------

        score += self._direction_score(
            opportunity,
            instrument,
        )

        # -----------------------------------------------------
        # 5. Horizon implementation quality
        # -----------------------------------------------------

        score += self._horizon_score(
            opportunity,
            instrument,
        )

        # -----------------------------------------------------
        # 6. Leverage / complexity penalties
        # -----------------------------------------------------

        score += self._risk_adjustment(
            opportunity,
            instrument,
        )

        score = max(
            0.0,
            min(
                1.0,
                score,
            ),
        )

        return SelectionDecision(
            eligible=True,
            rejection_reason=None,
            suitability_score=score,
        )

    # =========================================================
    # Direction
    # =========================================================

    def _direction_compatibility(
        self,
        opportunity: TradeOpportunity,
        instrument: FinecoInstrument,
    ) -> SelectionDecision | None:

        relationship = (
            instrument.exposure_relationship
        )

        # -----------------------------------------------------
        # LONG opportunity
        # -----------------------------------------------------

        if opportunity.direction == Direction.LONG:

            if relationship == ExposureRelationship.INVERSE:
                return SelectionDecision(
                    eligible=False,
                    rejection_reason=(
                        "Inverse instrument is incompatible "
                        "with LONG opportunity."
                    ),
                    suitability_score=0.0,
                )

            if relationship in {
                ExposureRelationship.DIRECT,
                ExposureRelationship.LEVERAGED_LONG,
            }:
                if instrument.long_available is False:
                    return SelectionDecision(
                        eligible=False,
                        rejection_reason=(
                            "Instrument is not available LONG."
                        ),
                        suitability_score=0.0,
                    )

                return None

            if relationship == ExposureRelationship.STRUCTURED:
                if instrument.long_available is False:
                    return SelectionDecision(
                        eligible=False,
                        rejection_reason=(
                            "Structured instrument is not "
                            "available LONG."
                        ),
                        suitability_score=0.0,
                    )

                return None

            return SelectionDecision(
                eligible=False,
                rejection_reason=(
                    "Unsupported exposure relationship "
                    "for LONG opportunity."
                ),
                suitability_score=0.0,
            )

        # -----------------------------------------------------
        # SHORT opportunity
        # -----------------------------------------------------

        if opportunity.direction == Direction.SHORT:

            # Direct exposure requires broker short capability.
            if relationship == ExposureRelationship.DIRECT:

                if instrument.short_available is not True:
                    return SelectionDecision(
                        eligible=False,
                        rejection_reason=(
                            "Direct instrument requires confirmed "
                            "SHORT availability."
                        ),
                        suitability_score=0.0,
                    )

                return None

            # An inverse product is normally bought LONG to
            # implement a bearish view.
            if relationship == ExposureRelationship.INVERSE:

                if instrument.long_available is not True:
                    return SelectionDecision(
                        eligible=False,
                        rejection_reason=(
                            "Inverse instrument must be available "
                            "LONG to implement SHORT exposure."
                        ),
                        suitability_score=0.0,
                    )

                return None

            if (
                relationship
                == ExposureRelationship.LEVERAGED_LONG
            ):
                return SelectionDecision(
                    eligible=False,
                    rejection_reason=(
                        "Leveraged-long instrument is incompatible "
                        "with SHORT opportunity."
                    ),
                    suitability_score=0.0,
                )

            # Structured certificates may represent bearish or
            # non-linear payoffs, but the current cache does not
            # always contain enough payoff detail to prove it.
            if relationship == ExposureRelationship.STRUCTURED:

                return SelectionDecision(
                    eligible=False,
                    rejection_reason=(
                        "Structured payoff is not sufficiently "
                        "classified to confirm SHORT compatibility."
                    ),
                    suitability_score=0.0,
                )

            return SelectionDecision(
                eligible=False,
                rejection_reason=(
                    "Unsupported exposure relationship "
                    "for SHORT opportunity."
                ),
                suitability_score=0.0,
            )

        return SelectionDecision(
            eligible=False,
            rejection_reason=(
                "Unsupported opportunity direction."
            ),
            suitability_score=0.0,
        )

    # =========================================================
    # Horizon
    # =========================================================

    def _horizon_compatibility(
        self,
        opportunity: TradeOpportunity,
        instrument: FinecoInstrument,
    ) -> SelectionDecision | None:

        mode = (
            instrument.trading_mode
        )

        # -----------------------------------------------------
        # INTRADAY opportunity
        # -----------------------------------------------------

        if opportunity.horizon == TradingHorizon.INTRADAY:

            if (
                mode == TradingMode.OVERNIGHT
                and instrument.intraday_available is not True
            ):
                return SelectionDecision(
                    eligible=False,
                    rejection_reason=(
                        "Overnight-only instrument is incompatible "
                        "with INTRADAY opportunity."
                    ),
                    suitability_score=0.0,
                )

            return None

        # -----------------------------------------------------
        # SWING / TACTICAL
        # -----------------------------------------------------

        if opportunity.horizon in {
            TradingHorizon.SWING,
            TradingHorizon.TACTICAL,
        }:

            if mode == TradingMode.INTRADAY:
                return SelectionDecision(
                    eligible=False,
                    rejection_reason=(
                        "Intraday-only instrument cannot implement "
                        "a multi-session opportunity."
                    ),
                    suitability_score=0.0,
                )

            if mode == TradingMode.SUPER_LEVERAGE:
                return SelectionDecision(
                    eligible=False,
                    rejection_reason=(
                        "Super-leverage mode is not suitable for "
                        "multi-session holding."
                    ),
                    suitability_score=0.0,
                )

            if (
                instrument.overnight_available is False
                and mode
                not in {
                    TradingMode.ORDINARY,
                    TradingMode.MULTIDAY,
                }
            ):
                return SelectionDecision(
                    eligible=False,
                    rejection_reason=(
                        "Instrument does not support overnight "
                        "holding."
                    ),
                    suitability_score=0.0,
                )

            return None

        return None

    # =========================================================
    # Scoring
    # =========================================================

    def _direction_score(
        self,
        opportunity: TradeOpportunity,
        instrument: FinecoInstrument,
    ) -> float:

        relationship = (
            instrument.exposure_relationship
        )

        if opportunity.direction == Direction.LONG:

            if relationship == ExposureRelationship.DIRECT:
                return 0.15

            if (
                relationship
                == ExposureRelationship.LEVERAGED_LONG
            ):
                return 0.10

            if relationship == ExposureRelationship.STRUCTURED:
                return -0.05

        if opportunity.direction == Direction.SHORT:

            if relationship == ExposureRelationship.INVERSE:
                return 0.15

            if relationship == ExposureRelationship.DIRECT:
                return 0.10

        return 0.0

    def _horizon_score(
        self,
        opportunity: TradeOpportunity,
        instrument: FinecoInstrument,
    ) -> float:

        mode = (
            instrument.trading_mode
        )

        # Intraday trade
        if opportunity.horizon == TradingHorizon.INTRADAY:

            if mode == TradingMode.INTRADAY:
                return 0.20

            if mode == TradingMode.ORDINARY:
                return 0.10

            if mode == TradingMode.SUPER_LEVERAGE:
                return 0.05

            return 0.0

        # Swing / tactical
        if opportunity.horizon in {
            TradingHorizon.SWING,
            TradingHorizon.TACTICAL,
        }:

            if mode == TradingMode.ORDINARY:
                return 0.20

            if mode in {
                TradingMode.OVERNIGHT,
                TradingMode.MULTIDAY,
            }:
                return 0.15

            return 0.0

        return 0.0

    def _risk_adjustment(
        self,
        opportunity: TradeOpportunity,
        instrument: FinecoInstrument,
    ) -> float:

        adjustment = 0.0

        broker_leverage = (
            instrument.broker_leverage
        )

        embedded_leverage = (
            instrument.embedded_leverage
        )

        # -----------------------------------------------------
        # Broker leverage
        # -----------------------------------------------------

        if broker_leverage is not None:

            if broker_leverage > 20:
                adjustment -= 0.25

            elif broker_leverage > 10:
                adjustment -= 0.15

            elif broker_leverage > 5:
                adjustment -= 0.08

            elif broker_leverage > 1:
                adjustment -= 0.03

        # -----------------------------------------------------
        # Embedded leverage
        # -----------------------------------------------------

        if embedded_leverage is not None:

            if embedded_leverage >= 5:
                adjustment -= 0.20

            elif embedded_leverage >= 3:
                adjustment -= 0.12

            elif embedded_leverage >= 2:
                adjustment -= 0.06

        # -----------------------------------------------------
        # Instrument complexity
        # -----------------------------------------------------

        if (
            instrument.instrument_type
            == InstrumentType.CERTIFICATE
        ):
            adjustment -= 0.10

        if (
            instrument.exposure_relationship
            == ExposureRelationship.STRUCTURED
        ):
            adjustment -= 0.10

        return adjustment