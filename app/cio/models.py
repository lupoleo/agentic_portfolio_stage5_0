from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class Stage3Model(BaseModel):
    """Base configuration for all Stage 3.0 canonical contracts."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
        populate_by_name=True,
    )


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExecutionSide(str, Enum):
    BUY = "BUY"
    SELL_SHORT = "SELL_SHORT"


class TradingHorizon(str, Enum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"
    TACTICAL = "TACTICAL"


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    REJECTED_BY_OPERATOR = "REJECTED_BY_OPERATOR"
    EXECUTED_CONFIRMED = "EXECUTED_CONFIRMED"
    SUPERSEDED = "SUPERSEDED"


class OpportunityStatus(str, Enum):
    """
    Lifecycle of a CIO-discovered trading opportunity.
    """

    DISCOVERED = "DISCOVERED"
    UNDER_REVIEW = "UNDER_REVIEW"

    WAITING_FOR_BROKER_INSTRUMENTS = (
        "WAITING_FOR_BROKER_INSTRUMENTS"
    )

    READY_FOR_INSTRUMENT_SELECTION = (
        "READY_FOR_INSTRUMENT_SELECTION"
    )

    INSTRUMENTS_RANKED = "INSTRUMENTS_RANKED"

    POSITION_SIZED = "POSITION_SIZED"

    READY_FOR_PROPOSAL = "READY_FOR_PROPOSAL"

    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class InstrumentType(str, Enum):
    ORDINARY = "ORDINARY"
    MARGIN = "MARGIN"
    CFD = "CFD"
    CFDC = "CFDC"
    ETF = "ETF"
    ETC = "ETC"
    ETN = "ETN"
    CERTIFICATE = "CERTIFICATE"
    OTHER = "OTHER"


class TradingMode(str, Enum):
    ORDINARY = "ORDINARY"
    INTRADAY = "INTRADAY"
    OVERNIGHT = "OVERNIGHT"
    MULTIDAY = "MULTIDAY"
    SUPER_LEVERAGE = "SUPER_LEVERAGE"
    UNKNOWN = "UNKNOWN"


class ExposureRelationship(str, Enum):
    """
    Economic relationship between a Fineco instrument and the reference asset.

    DIRECT
        Direct exposure to the reference asset, including ordinary,
        margin and CFD/CFDC implementations.

    LEVERAGED_LONG
        Long product whose return is designed to amplify positive moves
        of the reference asset, e.g. a daily 2x long ETF.

    INVERSE
        Product whose intended directional exposure is inverse to the
        reference asset, e.g. a bear / inverse ETF.

    STRUCTURED
        Structured product whose payoff cannot be represented as a simple
        direct or inverse exposure, e.g. certificates.

    OTHER
        Relationship known to be non-standard but not yet classified.
    """

    DIRECT = "DIRECT"
    LEVERAGED_LONG = "LEVERAGED_LONG"
    INVERSE = "INVERSE"
    STRUCTURED = "STRUCTURED"
    OTHER = "OTHER"


class Currency(str, Enum):
    EUR = "EUR"
    USD = "USD"
    CAD = "CAD"
    GBP = "GBP"
    CHF = "CHF"
    JPY = "JPY"


class DataSource(str, Enum):
    FINECO = "FINECO"
    OPERATOR = "OPERATOR"
    MARKET_DATA = "MARKET_DATA"
    NEWS_PROVIDER = "NEWS_PROVIDER"
    OFFICIAL_SOURCE = "OFFICIAL_SOURCE"


class CacheStatus(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class CioDecisionType(str, Enum):
    ACCEPT = "ACCEPT"
    MODIFY = "MODIFY"
    REJECT = "REJECT"


class CioDecisionStatus(str, Enum):
    PRELIMINARY = "PRELIMINARY"
    OPERATOR_CONFIRMED = "OPERATOR_CONFIRMED"
    OPERATOR_OVERRIDDEN = "OPERATOR_OVERRIDDEN"
    SUPERSEDED = "SUPERSEDED"


class ExecutionPlanStatus(str, Enum):
    """
    Lifecycle of a broker-ready manual execution instruction.

    WAITING_FOR_OPERATOR_CONFIRMATION
        The CIO has approved the proposal and the plan has been
        materialized, but no broker execution has been confirmed.

    OPERATOR_CONFIRMED
        The human operator explicitly confirmed that execution took
        place. This status must never be set automatically by the CIO.

    CANCELLED
        The operator cancelled the plan before execution.

    SUPERSEDED
        A newer ExecutionPlan replaced this one.
    """

    WAITING_FOR_OPERATOR_CONFIRMATION = (
        "WAITING_FOR_OPERATOR_CONFIRMATION"
    )
    OPERATOR_CONFIRMED = "OPERATOR_CONFIRMED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class OperatorConfirmationOutcome(str, Enum):
    """
    Explicit human-reported outcome of a manual broker instruction.
    """

    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


class TradeOutcomeStatus(str, Enum):
    """
    Lifecycle of an actually executed CIO trade.

    OPEN
        Broker execution was explicitly confirmed by the operator and
        the resulting position remains open.

    CLOSED
        The executed trade has subsequently been closed and realized
        exit economics may be recorded.
    """

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TradeExitReason(str, Enum):
    """
    Operator-recorded reason for closing an executed CIO trade.
    """

    TARGET_1 = "TARGET_1"
    TARGET_2 = "TARGET_2"
    STOP = "STOP"
    MANUAL = "MANUAL"
    TIME_EXIT = "TIME_EXIT"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    EVENT_COMPLETED = "EVENT_COMPLETED"
    OTHER = "OTHER"


class DecisionEvidenceType(str, Enum):
    HARD_CONSTRAINT = "HARD_CONSTRAINT"
    ANALYTICAL_WARNING = "ANALYTICAL_WARNING"
    PORTFOLIO_FIT = "PORTFOLIO_FIT"
    TRADE_STRUCTURE = "TRADE_STRUCTURE"
    MARKET_CONTEXT = "MARKET_CONTEXT"
    OTHER = "OTHER"


class DecisionEvidenceOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    WARNING = "WARNING"


class RequiredChangeType(str, Enum):
    ADD_STOP = "ADD_STOP"
    PROVIDE_PORTFOLIO_NAV = "PROVIDE_PORTFOLIO_NAV"
    REDUCE_SIZE = "REDUCE_SIZE"
    CHANGE_INSTRUMENT = "CHANGE_INSTRUMENT"
    CHANGE_ENTRY = "CHANGE_ENTRY"
    CHANGE_TARGET = "CHANGE_TARGET"
    REFRESH_DATA = "REFRESH_DATA"
    RECOMPUTE_RISK = "RECOMPUTE_RISK"
    OTHER = "OTHER"




class PortfolioDirectionalEffect(str, Enum):
    DIVERSIFICATION = "DIVERSIFICATION"
    CONCENTRATION = "CONCENTRATION"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class PortfolioExposureContext(Stage3Model):
    long_exposure_before_eur: float = Field(ge=0)
    short_exposure_before_eur: float = Field(ge=0)
    gross_exposure_before_eur: float = Field(ge=0)
    net_exposure_before_eur: float

    long_exposure_after_eur: float | None = Field(default=None, ge=0)
    short_exposure_after_eur: float | None = Field(default=None, ge=0)
    gross_exposure_after_eur: float | None = Field(default=None, ge=0)
    net_exposure_after_eur: float | None = None

    gross_exposure_before_pct_nav: float | None = Field(default=None, ge=0)
    gross_exposure_after_pct_nav: float | None = Field(default=None, ge=0)

    absolute_net_exposure_before_eur: float = Field(ge=0)
    absolute_net_exposure_after_eur: float | None = Field(default=None, ge=0)

    directional_effect: PortfolioDirectionalEffect

    @model_validator(mode="after")
    def validate_exposure_context(self) -> "PortfolioExposureContext":
        tol = 0.01
        if abs((self.long_exposure_before_eur + self.short_exposure_before_eur) - self.gross_exposure_before_eur) > tol:
            raise ValueError("gross_exposure_before_eur must equal long_exposure_before_eur + short_exposure_before_eur")
        if abs((self.long_exposure_before_eur - self.short_exposure_before_eur) - self.net_exposure_before_eur) > tol:
            raise ValueError("net_exposure_before_eur must equal long_exposure_before_eur - short_exposure_before_eur")
        if abs(abs(self.net_exposure_before_eur) - self.absolute_net_exposure_before_eur) > tol:
            raise ValueError("absolute_net_exposure_before_eur must equal abs(net_exposure_before_eur)")

        projected = (
            self.long_exposure_after_eur,
            self.short_exposure_after_eur,
            self.gross_exposure_after_eur,
            self.net_exposure_after_eur,
            self.absolute_net_exposure_after_eur,
        )

        if self.directional_effect == PortfolioDirectionalEffect.UNKNOWN:
            if any(value is not None for value in projected):
                raise ValueError("UNKNOWN directional_effect requires projected exposure fields to be None")
            return self

        if any(value is None for value in projected):
            raise ValueError("Known directional_effect requires all projected exposure fields")

        if abs((self.long_exposure_after_eur + self.short_exposure_after_eur) - self.gross_exposure_after_eur) > tol:
            raise ValueError("gross_exposure_after_eur must equal long_exposure_after_eur + short_exposure_after_eur")
        if abs((self.long_exposure_after_eur - self.short_exposure_after_eur) - self.net_exposure_after_eur) > tol:
            raise ValueError("net_exposure_after_eur must equal long_exposure_after_eur - short_exposure_after_eur")
        if abs(abs(self.net_exposure_after_eur) - self.absolute_net_exposure_after_eur) > tol:
            raise ValueError("absolute_net_exposure_after_eur must equal abs(net_exposure_after_eur)")
        return self


class PortfolioMetricDelta(Stage3Model):
    before: float
    after: float
    delta: float

    @model_validator(mode="after")
    def validate_delta(self) -> "PortfolioMetricDelta":
        if abs((self.after - self.before) - self.delta) > 1e-8:
            raise ValueError("delta must equal after - before")
        return self


class PortfolioMarginalRiskContext(Stage3Model):
    candidate_symbol: str = Field(min_length=1)
    volatility_pct: PortfolioMetricDelta
    beta: PortfolioMetricDelta
    var_95_1d_eur: PortfolioMetricDelta
    cvar_95_1d_eur: PortfolioMetricDelta

    candidate_correlation_to_portfolio: float | None = Field(default=None, ge=-1, le=1)
    candidate_component_risk_pct_points: float | None = None
    candidate_risk_contribution_pct: float | None = None

    analytical_coverage_before_pct: float = Field(ge=0, le=100)
    analytical_coverage_after_pct: float = Field(ge=0, le=100)

    excluded_symbols_before: tuple[str, ...] = ()
    excluded_symbols_after: tuple[str, ...] = ()
    observations_before: int = Field(ge=0)
    observations_after: int = Field(ge=0)


class PortfolioFitDecision(str, Enum):
    PASS = "PASS"
    PASS_WITH_WARNING = "PASS_WITH_WARNING"
    REJECT = "REJECT"
    UNKNOWN = "UNKNOWN"


class PortfolioCheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PortfolioConstraintCheck(Stage3Model):
    code: str = Field(min_length=1)
    status: PortfolioCheckStatus
    current_value: float | None = None
    projected_value: float | None = None
    limit_value: float | None = None
    unit: str | None = None
    reason: str | None = None



# PF-1D.3 scoring diagnostics contracts
class PortfolioFitScoreComponent(Stage3Model):
    name: str = Field(min_length=1)
    weight: float = Field(gt=0, le=1)
    score: float | None = Field(default=None, ge=0, le=100)
    reason: str = Field(min_length=1)


class PortfolioFitScoreBreakdown(Stage3Model):
    scoring_coverage_pct: float = Field(ge=0, le=100)
    analytical_coverage_pct: float | None = Field(default=None, ge=0, le=100)
    components: list[PortfolioFitScoreComponent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_component_names(self) -> "PortfolioFitScoreBreakdown":
        names = [item.name for item in self.components]
        if len(names) != len(set(names)):
            raise ValueError("portfolio fit score component names must be unique")
        return self


class PortfolioFitAssessment(Stage3Model):
    assessment_id: str = Field(min_length=1)
    opportunity_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    risk_state_id: str | None = None
    account_state_id: str | None = None
    created_at: datetime
    ticker: str = Field(min_length=1)
    direction: Direction
    target_exposure_eur: float | None = Field(default=None, gt=0)
    decision: PortfolioFitDecision
    constraint_checks: list[PortfolioConstraintCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    portfolio_fit_score: float | None = Field(default=None, ge=0, le=100)
    rationale: str = Field(min_length=1)

    exposure_context: PortfolioExposureContext | None = None
    marginal_risk_context: PortfolioMarginalRiskContext | None = None
    score_breakdown: PortfolioFitScoreBreakdown | None = None

    @property
    def hard_constraints_passed(self) -> bool | None:
        statuses = {check.status for check in self.constraint_checks}
        if PortfolioCheckStatus.FAIL in statuses:
            return False
        if PortfolioCheckStatus.UNKNOWN in statuses:
            return None
        return True

    @model_validator(mode="after")
    def validate_portfolio_fit_assessment(self) -> "PortfolioFitAssessment":
        codes = [check.code for check in self.constraint_checks]
        if len(codes) != len(set(codes)):
            raise ValueError("constraint check codes must be unique")
        statuses = {check.status for check in self.constraint_checks}
        has_fail = PortfolioCheckStatus.FAIL in statuses
        has_unknown = PortfolioCheckStatus.UNKNOWN in statuses
        has_pass = PortfolioCheckStatus.PASS in statuses
        if has_fail and self.decision != PortfolioFitDecision.REJECT:
            raise ValueError("PortfolioFitAssessment must be REJECT when a constraint check has status=FAIL")
        if self.decision == PortfolioFitDecision.REJECT and not has_fail:
            score_reject = (
                self.portfolio_fit_score is not None
                and self.portfolio_fit_score < 35.0
            )
            if not score_reject:
                raise ValueError(
                    "REJECT requires either a FAIL constraint check "
                    "or portfolio_fit_score < 35"
                )
        if self.decision == PortfolioFitDecision.PASS and (has_fail or has_unknown or self.warnings):
            raise ValueError("PASS is not allowed with FAIL/UNKNOWN checks or warnings")
        if self.decision == PortfolioFitDecision.PASS_WITH_WARNING and has_fail:
            raise ValueError("PASS_WITH_WARNING is not allowed with FAIL checks")
        if self.decision == PortfolioFitDecision.UNKNOWN:
            scoring_insufficient = (
                self.score_breakdown is not None
                and self.score_breakdown.scoring_coverage_pct < 70.0
            )
            if has_fail:
                raise ValueError("UNKNOWN is not allowed with FAIL checks")
            if has_pass and not scoring_insufficient:
                raise ValueError(
                    "UNKNOWN with PASS checks requires scoring_coverage_pct < 70"
                )
        return self


class CurrencyCash(Stage3Model):
    currency: Currency
    available: float = Field(ge=0)
    reserve: float = Field(default=0.0, ge=0)
    buying_power: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def reserve_cannot_exceed_available(self) -> "CurrencyCash":
        if self.reserve > self.available:
            raise ValueError(
                "reserve cannot exceed available cash"
            )
        return self


class RiskConstraints(Stage3Model):
    """
    Canonical CIO risk-policy constraints.

    max_portfolio_gross_exposure_pct
        Maximum total portfolio gross exposure as a percentage of
        canonical portfolio NAV / account equity.

        Backward compatibility:
        older persisted payloads may still contain
        "max_gross_exposure_pct".

    max_cio_deployable_pct
        Maximum percentage of currently deployable capital that the CIO
        may commit to a new opportunity.
    """

    max_trade_loss_eur: float | None = Field(
        default=None,
        ge=0,
    )

    max_position_weight_pct: float | None = Field(
        default=None,
        ge=0,
    )

    max_portfolio_gross_exposure_pct: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "max_portfolio_gross_exposure_pct",
            "max_gross_exposure_pct",
        ),
    )

    max_cio_deployable_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    max_portfolio_beta: float | None = None

    max_var_95_1d_eur: float | None = Field(
        default=None,
        ge=0,
    )

    min_cash_reserve_eur: float | None = Field(
        default=None,
        ge=0,
    )

    min_cash_reserve_usd: float | None = Field(
        default=None,
        ge=0,
    )

    @property
    def max_gross_exposure_pct(
        self,
    ) -> float | None:
        """
        Backward-compatible read alias.
        New code should use max_portfolio_gross_exposure_pct.
        """
        return self.max_portfolio_gross_exposure_pct


class AccountState(Stage3Model):
    """
    Canonical CIO account state.

    account_equity_eur
        Canonical portfolio NAV / account equity expressed in EUR.

        This value is intentionally separate from available cash and
        buying power. It is used as the denominator for portfolio-level
        percentage constraints such as
        max_portfolio_gross_exposure_pct.

        None preserves backward compatibility with older persisted
        AccountState records and explicitly means that NAV/equity is
        not yet available.
    """

    account_state_id: str = Field(
        min_length=1
    )
    timestamp: datetime

    cash: list[CurrencyCash]

    account_equity_eur: float | None = Field(
        default=None,
        gt=0,
    )

    constraints: RiskConstraints = Field(
        default_factory=RiskConstraints
    )

    source: DataSource
    notes: str | None = None

    @property
    def canonical_nav_eur(
        self,
    ) -> float | None:
        """
        Semantic alias used by risk engines.
        """
        return self.account_equity_eur

    @model_validator(mode="after")
    def unique_cash_currencies(
        self,
    ) -> "AccountState":

        currencies = [
            item.currency
            for item in self.cash
        ]

        if len(currencies) != len(
            set(currencies)
        ):
            raise ValueError(
                "cash currencies must be unique"
            )

        return self


class PortfolioSnapshot(Stage3Model):
    snapshot_id: str = Field(
        min_length=1
    )
    timestamp: datetime
    source_file: str = Field(
        min_length=1
    )
    source_file_hash: str = Field(
        min_length=1
    )
    quant_engine_version: str = Field(
        min_length=1
    )
    analyzed_positions: int = Field(
        ge=0
    )
    gross_exposure_eur: float = Field(
        ge=0
    )
    net_exposure_eur: float
    account_state_id: str | None = None


class InstrumentMarketSnapshot(Stage3Model):
    observed_at: datetime
    currency: Currency
    bid: float | None = Field(
        default=None,
        ge=0,
    )
    ask: float | None = Field(
        default=None,
        ge=0,
    )
    spread: float | None = Field(
        default=None,
        ge=0,
    )

    @model_validator(mode="after")
    def validate_bid_ask(
        self,
    ) -> "InstrumentMarketSnapshot":

        if (
            self.bid is not None
            and self.ask is not None
            and self.ask < self.bid
        ):
            raise ValueError(
                "ask cannot be lower than bid"
            )

        return self


class CertificateTerms(Stage3Model):
    """
    Optional structured terms for a Fineco certificate.

    Unknown fields remain None. The Instrument Selector must not infer
    missing payoff characteristics.
    """

    issuer: str | None = None
    certificate_type: str | None = None
    maturity_date: date | None = None

    strike: float | None = Field(
        default=None,
        ge=0,
    )

    barrier: float | None = Field(
        default=None,
        ge=0,
    )

    barrier_pct: float | None = Field(
        default=None,
        ge=0,
    )

    cap: float | None = Field(
        default=None,
        ge=0,
    )

    ratio: float | None = Field(
        default=None,
        gt=0,
    )

    participation_pct: float | None = Field(
        default=None,
        ge=0,
    )

    coupon_pct: float | None = None
    isin: str | None = None
    payoff_notes: str | None = None


class FinecoInstrument(Stage3Model):
    """
    One Fineco tradable instrument / operating mode.

    Existing V2 cache records remain valid:
    - exposure_relationship defaults to DIRECT;
    - reference_underlying may remain None and consumers should fall back
      to `underlying`.

    Related instruments (ETF/ETN/certificates) should explicitly set:
        reference_underlying
        exposure_relationship
    """

    instrument_id: str = Field(
        min_length=1
    )

    # Logical asset family used by the CIO cache lookup.
    # For GOOGL-related products this remains "GOOGL".
    underlying: str = Field(
        min_length=1
    )

    # Explicit economic reference asset.
    reference_underlying: str | None = None

    exposure_relationship: ExposureRelationship = (
        ExposureRelationship.DIRECT
    )

    description: str | None = None
    instrument_type: InstrumentType
    trading_mode: TradingMode = (
        TradingMode.UNKNOWN
    )

    fineco_symbol: str | None = None
    market: str | None = None

    quote_currency: Currency = Field(
        validation_alias=AliasChoices(
            "quote_currency",
            "currency",
        )
    )

    settlement_currency: Currency | None = None
    margin_currency: Currency | None = None

    long_available: bool | None = None
    short_available: bool | None = None

    intraday_available: bool | None = None
    overnight_available: bool | None = None

    # Leverage supplied by Fineco / broker.
    # Backward compatibility:
    # old persisted JSON may still contain "leverage".
    broker_leverage: float | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices(
            "broker_leverage",
            "leverage",
        ),
    )

    # Leverage structurally embedded in the product itself.
    embedded_leverage: float | None = Field(
        default=1.0,
        gt=0,
    )

    margin_pct: float | None = Field(
        default=None,
        ge=0,
    )

    tick_size: float | None = Field(
        default=None,
        gt=0,
    )

    tick_currency: Currency | None = None

    commission: float | None = Field(
        default=None,
        ge=0,
    )

    overnight_financing_pct: float | None = None

    certificate_terms: CertificateTerms | None = None
    market_snapshot: InstrumentMarketSnapshot | None = None

    last_confirmed: datetime | None = None
    source: DataSource
    cache_status: CacheStatus
    notes: str | None = None

    @property
    def currency(self) -> Currency:
        """
        Backward-compatible alias.
        New code should use quote_currency.
        """
        return self.quote_currency

    @property
    def effective_reference_underlying(
        self,
    ) -> str:
        return (
            self.reference_underlying
            or self.underlying
        ).upper()

    @property
    def leverage(
        self,
    ) -> float | None:
        """
        Backward-compatible alias.

        Old code can still use:
            instrument.leverage

        New code should use:
            instrument.broker_leverage
            instrument.embedded_leverage
        """
        return self.broker_leverage

    @model_validator(mode="after")
    def validate_instrument_consistency(
        self,
    ) -> "FinecoInstrument":

        if (
            self.trading_mode
            == TradingMode.INTRADAY
            and self.intraday_available is False
        ):
            raise ValueError(
                "INTRADAY trading mode cannot have "
                "intraday_available=False"
            )

        if (
            self.trading_mode
            in {
                TradingMode.OVERNIGHT,
                TradingMode.MULTIDAY,
            }
            and self.overnight_available is False
        ):
            raise ValueError(
                "OVERNIGHT/MULTIDAY trading mode cannot have "
                "overnight_available=False"
            )

        if (
            self.tick_currency is not None
            and self.tick_size is None
        ):
            raise ValueError(
                "tick_currency requires tick_size"
            )

        if (
            self.instrument_type
            == InstrumentType.CERTIFICATE
            and self.exposure_relationship
            not in {
                ExposureRelationship.STRUCTURED,
                ExposureRelationship.OTHER,
            }
        ):
            raise ValueError(
                "CERTIFICATE instruments must use STRUCTURED "
                "or OTHER exposure_relationship"
            )

        if (
            self.exposure_relationship
            == ExposureRelationship.STRUCTURED
            and self.instrument_type
            != InstrumentType.CERTIFICATE
            and self.certificate_terms is not None
        ):
            raise ValueError(
                "certificate_terms are only valid for "
                "CERTIFICATE instruments"
            )

        return self


class NewsItem(Stage3Model):
    news_id: str = Field(
        min_length=1
    )
    published_at: datetime
    retrieved_at: datetime
    provider: str = Field(
        min_length=1
    )
    source: str = Field(
        min_length=1
    )
    source_tier: int = Field(
        ge=1
    )
    headline: str = Field(
        min_length=1
    )
    summary: str | None = None
    url: str | None = None
    tickers: list[str]
    topics: list[str] = Field(
        default_factory=list
    )
    event_type: str | None = None
    provider_sentiment: float | None = None
    relevance_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    impact_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    credibility_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )


class MarketEvent(Stage3Model):
    event_id: str = Field(
        min_length=1
    )
    event_type: str = Field(
        min_length=1
    )
    scheduled_at: datetime
    ticker: str | None = None
    country: str | None = None
    importance: int | None = Field(
        default=None,
        ge=0,
    )
    source: str = Field(
        min_length=1
    )
    description: str = Field(
        min_length=1
    )
    confirmed: bool = True


class TradeOpportunity(Stage3Model):
    """
    Direction-neutral trading opportunity discovered by the CIO.

    An opportunity exists independently from the Fineco Instrument Cache.

    Therefore the CIO may discover, rank and persist an opportunity even
    when the corresponding broker instruments have not yet been mapped.
    """

    opportunity_id: str = Field(
        min_length=1
    )

    snapshot_id: str = Field(
        min_length=1
    )

    created_at: datetime
    updated_at: datetime | None = None

    ticker: str = Field(
        min_length=1
    )

    direction: Direction
    horizon: TradingHorizon

    expected_holding_min_days: int | None = Field(
        default=None,
        ge=0,
    )

    expected_holding_max_days: int | None = Field(
        default=None,
        ge=0,
    )

    confidence: float = Field(
        ge=0,
        le=1,
    )

    target_exposure_eur: float | None = Field(
        default=None,
        gt=0,
    )

    max_intended_loss_eur: float | None = Field(
        default=None,
        ge=0,
    )

    thesis: str = Field(
        min_length=1
    )

    catalyst: str | None = None

    key_risks: list[str] = Field(
        default_factory=list
    )

    evidence_ids: list[str] = Field(
        default_factory=list
    )

    status: OpportunityStatus = (
        OpportunityStatus.DISCOVERED
    )

    broker_instruments_required: bool = True

    broker_instruments_available: bool | None = None

    broker_instrument_count: int | None = Field(
        default=None,
        ge=0,
    )

    rejection_reason: str | None = None
    expiry_reason: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_opportunity(
        self,
    ) -> "TradeOpportunity":

        if (
            self.expected_holding_min_days
            is not None
            and self.expected_holding_max_days
            is not None
            and self.expected_holding_min_days
            > self.expected_holding_max_days
        ):
            raise ValueError(
                "expected_holding_min_days cannot exceed "
                "expected_holding_max_days"
            )

        if (
            self.broker_instruments_available is False
            and self.broker_instrument_count
            not in {
                None,
                0,
            }
        ):
            raise ValueError(
                "broker_instrument_count must be 0 or None when "
                "broker_instruments_available=False"
            )

        if (
            self.broker_instruments_available is True
            and self.broker_instrument_count == 0
        ):
            raise ValueError(
                "broker_instrument_count cannot be 0 when "
                "broker_instruments_available=True"
            )

        if (
            self.status
            == OpportunityStatus.WAITING_FOR_BROKER_INSTRUMENTS
            and self.broker_instruments_available is True
        ):
            raise ValueError(
                "WAITING_FOR_BROKER_INSTRUMENTS cannot be used "
                "when broker instruments are already available"
            )

        if (
            self.status
            == OpportunityStatus.READY_FOR_INSTRUMENT_SELECTION
            and self.broker_instruments_required
            and self.broker_instruments_available is not True
        ):
            raise ValueError(
                "READY_FOR_INSTRUMENT_SELECTION requires "
                "available broker instruments"
            )

        if (
            self.status
            == OpportunityStatus.REJECTED
            and not self.rejection_reason
        ):
            raise ValueError(
                "rejection_reason is required when "
                "status=REJECTED"
            )

        if (
            self.status
            == OpportunityStatus.EXPIRED
            and not self.expiry_reason
        ):
            raise ValueError(
                "expiry_reason is required when "
                "status=EXPIRED"
            )

        return self


class InstrumentCandidate(Stage3Model):
    instrument_id: str = Field(
        min_length=1
    )
    opportunity_id: str = Field(
        min_length=1
    )
    eligible: bool
    rejection_reason: str | None = None

    estimated_margin_eur: float | None = Field(
        default=None,
        ge=0,
    )

    estimated_cost_eur: float | None = Field(
        default=None,
        ge=0,
    )

    suitability_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )

    @model_validator(mode="after")
    def rejection_requires_reason(
        self,
    ) -> "InstrumentCandidate":

        if (
            not self.eligible
            and not self.rejection_reason
        ):
            raise ValueError(
                "rejection_reason is required when "
                "candidate is not eligible"
            )

        return self


class PositionSizingResult(Stage3Model):
    sizing_id: str = Field(
        min_length=1
    )
    opportunity_id: str = Field(
        min_length=1
    )
    instrument_id: str = Field(
        min_length=1
    )

    created_at: datetime

    execution_side: ExecutionSide

    reference_price: float = Field(
        gt=0
    )
    currency: Currency

    # EUR value of one unit of quote currency.
    fx_to_eur: float = Field(
        gt=0
    )

    stop_price: float | None = Field(
        default=None,
        gt=0,
    )

    quantity: float = Field(
        gt=0
    )

    gross_exposure_eur: float = Field(
        ge=0
    )

    estimated_capital_required_eur: float = Field(
        ge=0
    )

    estimated_margin_eur: float | None = Field(
        default=None,
        ge=0,
    )

    estimated_max_loss_eur: float | None = Field(
        default=None,
        ge=0,
    )

    risk_budget_eur: float | None = Field(
        default=None,
        ge=0,
    )

    constraints_passed: bool

    violated_constraints: list[str] = Field(
        default_factory=list
    )

    notes: str | None = None

    @model_validator(mode="after")
    def validate_constraints(
        self,
    ) -> "PositionSizingResult":

        if (
            self.constraints_passed
            and self.violated_constraints
        ):
            raise ValueError(
                "violated_constraints must be empty "
                "when constraints_passed=True"
            )

        if (
            not self.constraints_passed
            and not self.violated_constraints
        ):
            raise ValueError(
                "violated_constraints is required "
                "when constraints_passed=False"
            )

        return self


class TradeProposal(Stage3Model):
    proposal_id: str = Field(
        min_length=1
    )
    opportunity_id: str = Field(
        min_length=1
    )
    snapshot_id: str = Field(
        min_length=1
    )
    created_at: datetime
    ticker: str = Field(
        min_length=1
    )
    direction: Direction
    instrument_id: str = Field(
        min_length=1
    )

    sizing_id: str | None = None

    execution_side: ExecutionSide | None = None

    quantity: float = Field(
        gt=0
    )

    reference_price: float = Field(
        gt=0
    )

    currency: Currency

    # EUR value of one unit of proposal currency used by PositionSizer.
    # Optional for backward compatibility with older persisted proposals.
    fx_to_eur: float | None = Field(
        default=None,
        gt=0,
    )

    entry_type: str = Field(
        min_length=1
    )

    entry_price: float | None = Field(
        default=None,
        gt=0,
    )

    stop_price: float | None = Field(
        default=None,
        gt=0,
    )

    target_1: float | None = Field(
        default=None,
        gt=0,
    )

    target_2: float | None = Field(
        default=None,
        gt=0,
    )

    gross_exposure_eur: float = Field(
        ge=0
    )

    # Capital/collateral requirement determined by PositionSizer.
    # Optional for backward compatibility with older persisted proposals.
    estimated_capital_required_eur: float | None = Field(
        default=None,
        ge=0,
    )

    estimated_margin_eur: float | None = Field(
        default=None,
        ge=0,
    )

    estimated_max_loss_eur: float | None = Field(
        default=None,
        ge=0,
    )

    expected_holding_min_days: int | None = Field(
        default=None,
        ge=0,
    )

    expected_holding_max_days: int | None = Field(
        default=None,
        ge=0,
    )

    status: ProposalStatus = (
        ProposalStatus.PROPOSED
    )

    @model_validator(mode="after")
    def validate_holding_range(
        self,
    ) -> "TradeProposal":

        if (
            self.expected_holding_min_days
            is not None
            and self.expected_holding_max_days
            is not None
            and self.expected_holding_min_days
            > self.expected_holding_max_days
        ):
            raise ValueError(
                "expected_holding_min_days cannot exceed "
                "expected_holding_max_days"
            )

        return self


class PortfolioRiskState(Stage3Model):
    """
    Canonical portfolio-risk state used by the CIO Portfolio Risk Simulator.

    Exposure conventions
    --------------------
    long_exposure_eur and short_exposure_eur are stored as positive
    absolute EUR amounts.

    Therefore:

        gross exposure = long exposure + short exposure

        net exposure = long exposure - short exposure

    analytical_coverage_pct records how much of the portfolio exposure
    is represented by the analytical/risk engine.

    This is intentionally separate from the raw PortfolioSnapshot because
    some instruments may not have sufficient market history or may not yet
    be mapped to a market-data provider.
    """

    gross_exposure_eur: float = Field(
        ge=0,
    )

    net_exposure_eur: float

    # Absolute positive amount of economically LONG exposure.
    #
    # Defaults preserve compatibility with older tests and persisted
    # PortfolioRiskState objects created before this field existed.
    long_exposure_eur: float = Field(
        default=0.0,
        ge=0,
    )

    # Absolute positive amount of economically SHORT exposure.
    short_exposure_eur: float = Field(
        default=0.0,
        ge=0,
    )

    portfolio_volatility_pct: float = Field(
        ge=0,
    )

    portfolio_beta: float

    var_95_1d_eur: float = Field(
        ge=0,
    )

    cvar_95_1d_eur: float = Field(
        ge=0,
    )

    top5_concentration_pct: float = Field(
        ge=0,
    )

    effective_positions: float = Field(
        ge=0,
    )

    # Percentage of portfolio exposure represented by the analytical
    # engine. None means that coverage has not yet been calculated.
    analytical_coverage_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
    )

class PortfolioRiskStateRecord(Stage3Model):
    """
    Persistable wrapper around a PortfolioRiskState.

    PortfolioRiskState remains a pure analytical value object.
    This record adds identity, provenance and timestamp so the
    Quant Engine can persist the canonical risk state associated
    with a PortfolioSnapshot.
    """

    risk_state_id: str = Field(
        min_length=1,
    )

    snapshot_id: str = Field(
        min_length=1,
    )

    created_at: datetime

    state: PortfolioRiskState


class PortfolioRiskDelta(Stage3Model):
    """
    Difference between simulated AFTER and BEFORE portfolio risk states.

    Unlike PortfolioRiskState, delta fields are intentionally signed:
    a negative value means that the proposed trade reduces that metric.
    """

    gross_exposure_eur: float
    net_exposure_eur: float

    long_exposure_eur: float = 0.0
    short_exposure_eur: float = 0.0

    portfolio_volatility_pct: float
    portfolio_beta: float

    var_95_1d_eur: float
    cvar_95_1d_eur: float

    top5_concentration_pct: float
    effective_positions: float

    analytical_coverage_pct: float | None = None

class PortfolioSimulation(Stage3Model):
    """
    Result of applying a TradeProposal hypothetically to a portfolio.

    A PortfolioSimulation is analytical only. It never modifies the real
    PortfolioSnapshot or represents broker execution.

    before
        Portfolio risk state before the proposed trade.

    after
        Hypothetical portfolio risk state after applying the trade.

    delta
        Difference between AFTER and BEFORE.

        For signed metrics such as net exposure or beta, the sign is
        meaningful.

        Exposure LONG/SHORT deltas describe the change in their respective
        absolute exposure buckets.

    constraints_passed
        True only when all constraints that can currently be evaluated
        have passed.

    violated_constraints
        Hard failures against configured CIO risk constraints.

    warnings
        Non-fatal analytical limitations. Examples include incomplete
        market-data coverage or risk metrics that cannot yet be
        recomputed.
    """

    simulation_id: str = Field(
        min_length=1,
    )

    snapshot_id: str = Field(
        min_length=1,
    )

    proposal_id: str = Field(
        min_length=1,
    )

    created_at: datetime

    before: PortfolioRiskState

    after: PortfolioRiskState

    delta: PortfolioRiskDelta

    cash_after_eur: float | None = Field(
        default=None,
        ge=0,
    )

    cash_after_usd: float | None = Field(
        default=None,
        ge=0,
    )

    constraints_passed: bool

    violated_constraints: list[str] = Field(
        default_factory=list,
    )

    # Warnings do NOT automatically cause constraints_passed=False.
    #
    # They represent analytical uncertainty or unavailable metrics,
    # rather than a known risk-policy violation.
    warnings: list[str] = Field(
        default_factory=list,
    )

    @model_validator(mode="after")
    def constraints_and_violations_are_consistent(
        self,
    ) -> "PortfolioSimulation":

        if (
            self.constraints_passed
            and self.violated_constraints
        ):
            raise ValueError(
                "violated_constraints must be empty "
                "when constraints_passed=True"
            )

        if (
            not self.constraints_passed
            and not self.violated_constraints
        ):
            raise ValueError(
                "violated_constraints is required "
                "when constraints_passed=False"
            )

        return self


class DecisionEvidenceAssessment(Stage3Model):
    """One auditable fact considered by the CIO Decision Engine."""

    evidence_type: DecisionEvidenceType
    code: str = Field(min_length=1)
    outcome: DecisionEvidenceOutcome
    summary: str = Field(min_length=1)
    critical: bool = False
    source_id: str | None = None


class RequiredDecisionChange(Stage3Model):
    """Structured remediation attached to a MODIFY decision."""

    change_type: RequiredChangeType
    description: str = Field(min_length=1)
    mandatory: bool = True


class DecisionEvidence(Stage3Model):
    evidence_id: str = Field(
        min_length=1
    )
    snapshot_id: str = Field(
        min_length=1
    )
    created_at: datetime
    ticker: str | None = None

    quantitative_summary: str = Field(
        min_length=1
    )

    technical_summary: str | None = None

    news_ids: list[str] = Field(
        default_factory=list
    )

    event_ids: list[str] = Field(
        default_factory=list
    )

    portfolio_fit_summary: str | None = None
    instrument_rationale: str | None = None
    risk_constraints_summary: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None


class CioDecision(Stage3Model):
    """
    Persistable output of the CIO Decision Engine.

    Governance:
    - known hard-constraint failures cannot result in ACCEPT;
    - MODIFY requires at least one structured required change;
    - incomplete critical evidence cannot result in ACCEPT;
    - broker execution remains manual and outside this contract.

    opportunity_id and snapshot_id are optional for backward compatibility
    with earlier Stage 3 persisted/test objects.
    """

    decision_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    simulation_id: str = Field(min_length=1)

    opportunity_id: str | None = None
    snapshot_id: str | None = None

    created_at: datetime
    decision: CioDecisionType

    confidence: float = Field(
        ge=0,
        le=1,
    )

    rationale: str = Field(min_length=1)

    evidence_ids: list[str] = Field(
        default_factory=list
    )

    assessments: list[DecisionEvidenceAssessment] = Field(
        default_factory=list
    )

    required_changes: list[RequiredDecisionChange] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(
        default_factory=list
    )

    hard_constraints_passed: bool | None = None
    critical_evidence_complete: bool | None = None

    status: CioDecisionStatus = (
        CioDecisionStatus.PRELIMINARY
    )

    operator_notes: str | None = None

    @model_validator(mode="after")
    def validate_decision_consistency(
        self,
    ) -> "CioDecision":

        if (
            self.decision == CioDecisionType.ACCEPT
            and self.hard_constraints_passed is False
        ):
            raise ValueError(
                "ACCEPT is not allowed when hard constraints failed"
            )

        if (
            self.decision == CioDecisionType.ACCEPT
            and self.critical_evidence_complete is False
        ):
            raise ValueError(
                "ACCEPT is not allowed when critical evidence is incomplete"
            )

        if (
            self.decision == CioDecisionType.MODIFY
            and not self.required_changes
        ):
            raise ValueError(
                "required_changes is required when decision=MODIFY"
            )

        failed_hard_constraints = [
            item
            for item in self.assessments
            if (
                item.evidence_type
                == DecisionEvidenceType.HARD_CONSTRAINT
                and item.outcome
                == DecisionEvidenceOutcome.FAIL
            )
        ]

        if (
            self.decision == CioDecisionType.ACCEPT
            and failed_hard_constraints
        ):
            raise ValueError(
                "ACCEPT is not allowed when a HARD_CONSTRAINT "
                "assessment has outcome=FAIL"
            )

        return self

class ExecutionPlan(Stage3Model):
    """
    Broker-ready manual execution instruction produced only after
    a CIO ACCEPT decision.

    This contract is the final bridge between CIO governance and
    manual broker execution. It does not execute an order.

    Provenance
    ----------
    opportunity_id, proposal_id, simulation_id, decision_id and
    snapshot_id identify the exact analytical chain that authorized
    this instruction.

    Broker instrument resolution
    ----------------------------
    instrument_id is the canonical locally cached broker instrument.
    The descriptive broker fields are deliberately denormalized into
    the plan so an operator can read the instruction without having to
    dereference internal IDs manually.

    Governance
    ----------
    A newly created plan must start in
    WAITING_FOR_OPERATOR_CONFIRMATION.

    OPERATOR_CONFIRMED may only be applied after an explicit human
    confirmation through a later operator workflow. No automatic
    broker execution is represented by this model.
    """

    execution_plan_id: str = Field(
        min_length=1,
    )

    created_at: datetime

    opportunity_id: str = Field(
        min_length=1,
    )

    proposal_id: str = Field(
        min_length=1,
    )

    simulation_id: str = Field(
        min_length=1,
    )

    decision_id: str = Field(
        min_length=1,
    )

    snapshot_id: str = Field(
        min_length=1,
    )

    broker: str = Field(
        min_length=1,
    )

    underlying: str = Field(
        min_length=1,
    )

    instrument_id: str = Field(
        min_length=1,
    )

    instrument_description: str | None = None
    broker_symbol: str | None = None
    market: str | None = None

    currency: Currency

    direction: Direction
    execution_side: ExecutionSide

    quantity: float = Field(
        gt=0,
    )

    order_type: str = Field(
        min_length=1,
    )

    reference_price: float | None = Field(
        default=None,
        gt=0,
    )

    entry_price: float | None = Field(
        default=None,
        gt=0,
    )

    stop_price: float | None = Field(
        default=None,
        gt=0,
    )

    target_1: float | None = Field(
        default=None,
        gt=0,
    )

    target_2: float | None = Field(
        default=None,
        gt=0,
    )

    fx_to_eur: float | None = Field(
        default=None,
        gt=0,
    )

    gross_exposure_eur: float = Field(
        ge=0,
    )

    estimated_capital_required_eur: float | None = Field(
        default=None,
        ge=0,
    )

    estimated_max_loss_eur: float | None = Field(
        default=None,
        ge=0,
    )

    expected_holding_min_days: int | None = Field(
        default=None,
        ge=0,
    )

    expected_holding_max_days: int | None = Field(
        default=None,
        ge=0,
    )

    status: ExecutionPlanStatus = (
        ExecutionPlanStatus.WAITING_FOR_OPERATOR_CONFIRMATION
    )

    execution_notes: str | None = None

    @model_validator(mode="after")
    def validate_execution_plan(
        self,
    ) -> "ExecutionPlan":

        if (
            self.expected_holding_min_days is not None
            and self.expected_holding_max_days is not None
            and self.expected_holding_min_days
            > self.expected_holding_max_days
        ):
            raise ValueError(
                "expected_holding_min_days cannot exceed "
                "expected_holding_max_days"
            )

        if (
            self.status
            == ExecutionPlanStatus.OPERATOR_CONFIRMED
        ):
            # The model may represent a later confirmed state, but
            # creation services must never create plans directly in it.
            # This validator intentionally does not forbid loading
            # already-confirmed persisted records.
            pass

        return self

class OperatorConfirmation(Stage3Model):
    """
    Immutable operator audit record describing what actually happened
    after a broker-ready ExecutionPlan was presented to the human operator.

    ExecutionPlan records what the CIO authorized.
    OperatorConfirmation records what the operator reports actually
    happened at the broker.

    The service layer, not this model, is responsible for changing the
    ExecutionPlan lifecycle status after persistence.
    """

    confirmation_id: str = Field(min_length=1)
    execution_plan_id: str = Field(min_length=1)
    created_at: datetime

    outcome: OperatorConfirmationOutcome

    executed_quantity: float | None = Field(
        default=None,
        gt=0,
    )

    executed_price: float | None = Field(
        default=None,
        gt=0,
    )

    commission_eur: float | None = Field(
        default=None,
        ge=0,
    )

    broker_order_reference: str | None = None
    notes: str | None = None

    source: DataSource = DataSource.OPERATOR

    @model_validator(mode="after")
    def validate_operator_confirmation(
        self,
    ) -> "OperatorConfirmation":

        if self.outcome == OperatorConfirmationOutcome.EXECUTED:
            if self.executed_quantity is None:
                raise ValueError(
                    "executed_quantity is required when "
                    "outcome=EXECUTED"
                )

            if self.executed_price is None:
                raise ValueError(
                    "executed_price is required when "
                    "outcome=EXECUTED"
                )

        if self.outcome == OperatorConfirmationOutcome.CANCELLED:
            if self.executed_quantity is not None:
                raise ValueError(
                    "executed_quantity must be None when "
                    "outcome=CANCELLED"
                )

            if self.executed_price is not None:
                raise ValueError(
                    "executed_price must be None when "
                    "outcome=CANCELLED"
                )

            if self.commission_eur is not None:
                raise ValueError(
                    "commission_eur must be None when "
                    "outcome=CANCELLED"
                )

            if self.broker_order_reference is not None:
                raise ValueError(
                    "broker_order_reference must be None when "
                    "outcome=CANCELLED"
                )

        return self

class TradeOutcome(Stage3Model):
    """
    Realized lifecycle record for an executed CIO trade.

    TradeOutcome connects the approved analytical/execution chain with
    actual broker results. V1 deliberately stores facts rather than
    analytical judgments such as thesis quality or CIO decision quality.

    Creation invariant
    ------------------
    A newly materialized TradeOutcome represents an actually executed
    trade and therefore starts OPEN with a positive actual entry
    quantity and entry price.

    Closure invariant
    -----------------
    CLOSED requires exit datetime, quantity, price and exit reason.
    OPEN must not contain realized exit fields or realized P/L metrics.
    """

    outcome_id: str = Field(
        min_length=1,
    )

    created_at: datetime
    updated_at: datetime

    # ---------------------------------------------------------
    # Provenance
    # ---------------------------------------------------------

    opportunity_id: str = Field(
        min_length=1,
    )

    proposal_id: str = Field(
        min_length=1,
    )

    simulation_id: str = Field(
        min_length=1,
    )

    decision_id: str = Field(
        min_length=1,
    )

    execution_plan_id: str = Field(
        min_length=1,
    )

    confirmation_id: str = Field(
        min_length=1,
    )

    snapshot_id: str = Field(
        min_length=1,
    )

    # ---------------------------------------------------------
    # Instrument / trade identity
    # ---------------------------------------------------------

    ticker: str = Field(
        min_length=1,
    )

    instrument_id: str = Field(
        min_length=1,
    )

    direction: Direction
    execution_side: ExecutionSide
    currency: Currency

    # ---------------------------------------------------------
    # Actual entry
    # ---------------------------------------------------------

    entry_datetime: datetime

    entry_quantity: float = Field(
        gt=0,
    )

    entry_price: float = Field(
        gt=0,
    )

    entry_commission_eur: float | None = Field(
        default=None,
        ge=0,
    )

    # EUR value of one unit of trade currency at entry.
    #
    # For EUR-denominated trades this may remain None because no FX
    # conversion is required. For non-EUR trades, close workflows should
    # persist this value so realized EUR economics can distinguish asset
    # P/L from currency P/L.
    entry_fx_to_eur: float | None = Field(
        default=None,
        gt=0,
    )

    # ---------------------------------------------------------
    # Approved plan snapshot
    # ---------------------------------------------------------

    planned_reference_price: float | None = Field(
        default=None,
        gt=0,
    )

    planned_stop_price: float | None = Field(
        default=None,
        gt=0,
    )

    planned_target_1: float | None = Field(
        default=None,
        gt=0,
    )

    planned_target_2: float | None = Field(
        default=None,
        gt=0,
    )

    planned_max_loss_eur: float | None = Field(
        default=None,
        ge=0,
    )

    expected_holding_min_days: int | None = Field(
        default=None,
        ge=0,
    )

    expected_holding_max_days: int | None = Field(
        default=None,
        ge=0,
    )

    # ---------------------------------------------------------
    # Realized exit
    # ---------------------------------------------------------

    exit_datetime: datetime | None = None

    exit_quantity: float | None = Field(
        default=None,
        gt=0,
    )

    exit_price: float | None = Field(
        default=None,
        gt=0,
    )

    exit_commission_eur: float | None = Field(
        default=None,
        ge=0,
    )

    # EUR value of one unit of trade currency at exit.
    #
    # Required by the close workflow for non-EUR trades so realized P/L
    # can be calculated using actual entry and exit FX independently.
    exit_fx_to_eur: float | None = Field(
        default=None,
        gt=0,
    )

    exit_reason: TradeExitReason | None = None

    # ---------------------------------------------------------
    # Realized economics
    # ---------------------------------------------------------

    realized_pnl_eur: float | None = None
    realized_return_pct: float | None = None

    holding_days: float | None = Field(
        default=None,
        ge=0,
    )

    # ---------------------------------------------------------
    # Execution quality
    # ---------------------------------------------------------

    entry_slippage_pct: float | None = None

    # ---------------------------------------------------------
    # State
    # ---------------------------------------------------------

    status: TradeOutcomeStatus = TradeOutcomeStatus.OPEN

    notes: str | None = None

    @model_validator(mode="after")
    def validate_trade_outcome(
        self,
    ) -> "TradeOutcome":

        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot be earlier than created_at"
            )

        if (
            self.expected_holding_min_days is not None
            and self.expected_holding_max_days is not None
            and self.expected_holding_min_days
            > self.expected_holding_max_days
        ):
            raise ValueError(
                "expected_holding_min_days cannot exceed "
                "expected_holding_max_days"
            )

        if self.status == TradeOutcomeStatus.OPEN:

            open_forbidden = {
                "exit_datetime": self.exit_datetime,
                "exit_quantity": self.exit_quantity,
                "exit_price": self.exit_price,
                "exit_commission_eur": self.exit_commission_eur,
                "exit_fx_to_eur": self.exit_fx_to_eur,
                "exit_reason": self.exit_reason,
                "realized_pnl_eur": self.realized_pnl_eur,
                "realized_return_pct": self.realized_return_pct,
                "holding_days": self.holding_days,
            }

            populated = [
                name
                for name, value in open_forbidden.items()
                if value is not None
            ]

            if populated:
                raise ValueError(
                    "OPEN TradeOutcome cannot contain realized "
                    "exit fields: "
                    + ", ".join(populated)
                )

        if self.status == TradeOutcomeStatus.CLOSED:

            required = {
                "exit_datetime": self.exit_datetime,
                "exit_quantity": self.exit_quantity,
                "exit_price": self.exit_price,
                "exit_reason": self.exit_reason,
            }

            missing = [
                name
                for name, value in required.items()
                if value is None
            ]

            if missing:
                raise ValueError(
                    "CLOSED TradeOutcome requires: "
                    + ", ".join(missing)
                )

            if (
                self.exit_datetime is not None
                and self.exit_datetime < self.entry_datetime
            ):
                raise ValueError(
                    "exit_datetime cannot be earlier than "
                    "entry_datetime"
                )

            if (
                self.exit_quantity is not None
                and self.exit_quantity != self.entry_quantity
            ):
                raise ValueError(
                    "CLOSED TradeOutcome requires full close: "
                    "exit_quantity must equal entry_quantity"
                )

            if (
                self.currency != Currency.EUR
                and self.entry_fx_to_eur is None
            ):
                raise ValueError(
                    "entry_fx_to_eur is required for CLOSED "
                    "non-EUR TradeOutcome"
                )

            if (
                self.currency != Currency.EUR
                and self.exit_fx_to_eur is None
            ):
                raise ValueError(
                    "exit_fx_to_eur is required for CLOSED "
                    "non-EUR TradeOutcome"
                )

        return self
