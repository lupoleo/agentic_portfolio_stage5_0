# Stage 3.0 — Data Contracts

## Purpose

This document defines the canonical Stage 3.0 data contracts between the frozen Stage 2.5 Quantitative Engine, Account State, Fineco Instrument Cache, Market Intelligence, CIO Decision Engine, Instrument Selector, Portfolio Simulator, reporting and audit layers.

Implementation uses **Pydantic v2** so the same contracts can be validated and serialized across Python, SQLite, JSON, LLM structured outputs and Excel reporting.

## Core rules

1. Fineco remains the authoritative source of real portfolio positions.
2. Proposed and simulated trades never mutate the real portfolio.
3. Python is authoritative for numerical financial calculations.
4. `None` / UNKNOWN is preferable to invented data.
5. Every CIO object is bound to a `PortfolioSnapshot`.
6. LONG / SHORT is explicit; order quantity remains a positive magnitude.
7. Persisted timestamps must be timezone-aware.

## Enumerations

```python
Direction = LONG | SHORT

TradingHorizon =
    INTRADAY
    SWING
    TACTICAL

ProposalStatus =
    PROPOSED
    REJECTED_BY_OPERATOR
    EXECUTED_CONFIRMED
    SUPERSEDED

InstrumentType =
    ORDINARY
    MARGIN
    CFD
    CFDC
    CERTIFICATE
    OTHER

Currency =
    EUR
    USD

DataSource =
    FINECO
    OPERATOR
    MARKET_DATA
    NEWS_PROVIDER
    OFFICIAL_SOURCE

CacheStatus =
    CURRENT
    STALE
    UNKNOWN

CioDecisionType =
    ACCEPT
    MODIFY
    REJECT
```

## CurrencyCash

Represents trading liquidity for one currency.

```python
class CurrencyCash(BaseModel):
    currency: Currency
    available: float
    reserve: float = 0.0
    buying_power: float | None = None
```

Validation:

- `available >= 0`
- `reserve >= 0`
- `reserve <= available`
- `buying_power >= 0` when known

Stage 3.0 initially supports EUR and USD.

## RiskConstraints

```python
class RiskConstraints(BaseModel):
    max_trade_loss_eur: float | None = None
    max_position_weight_pct: float | None = None
    max_gross_exposure_pct: float | None = None
    max_portfolio_beta: float | None = None
    max_var_95_1d_eur: float | None = None
    min_cash_reserve_eur: float | None = None
    min_cash_reserve_usd: float | None = None
```

`None` means no configured limit.

## AccountState

```python
class AccountState(BaseModel):
    account_state_id: str
    timestamp: datetime
    cash: list[CurrencyCash]
    constraints: RiskConstraints
    source: DataSource
    notes: str | None = None
```

Currencies must be unique inside one Account State.

A proposal does not modify Account State.

## PortfolioSnapshot

```python
class PortfolioSnapshot(BaseModel):
    snapshot_id: str
    timestamp: datetime
    source_file: str
    source_file_hash: str
    quant_engine_version: str
    analyzed_positions: int
    gross_exposure_eur: float
    net_exposure_eur: float
    account_state_id: str | None = None
```

`source_file_hash` uses SHA-256 to bind decisions to the exact Fineco export.

Recommended ID:

```text
SNAP-YYYYMMDD-HHMMSS-xxxxxx
```

## FinecoInstrument

```python
class FinecoInstrument(BaseModel):
    instrument_id: str
    underlying: str
    description: str | None
    instrument_type: InstrumentType
    fineco_symbol: str | None
    currency: Currency

    long_available: bool | None
    short_available: bool | None

    intraday_available: bool | None
    overnight_available: bool | None

    leverage: float | None
    margin_pct: float | None
    spread: float | None
    commission: float | None
    overnight_financing_pct: float | None

    last_confirmed: datetime | None
    source: DataSource
    cache_status: CacheStatus
    notes: str | None
```

No Fineco product availability is inferred from another ticker.

Natural-language operator input may be parsed into this contract, but missing values remain unknown.

## NewsItem

```python
class NewsItem(BaseModel):
    news_id: str
    published_at: datetime
    retrieved_at: datetime
    provider: str
    source: str
    source_tier: int
    headline: str
    summary: str | None
    url: str | None
    tickers: list[str]
    topics: list[str]
    event_type: str | None
    provider_sentiment: float | None
    relevance_score: float | None
    impact_score: float | None
    credibility_score: float | None
```

Recommended normalized CIO enrichment score range:

```text
0.0 .. 1.0
```

Source tier represents authority / reliability, not sentiment.

## MarketEvent

```python
class MarketEvent(BaseModel):
    event_id: str
    event_type: str
    scheduled_at: datetime
    ticker: str | None
    country: str | None
    importance: int | None
    source: str
    description: str
    confirmed: bool = True
```

Examples:

- EARNINGS
- CPI
- FOMC
- ECB
- JOBS
- DIVIDEND
- REGULATORY_EVENT
- COMPANY_EVENT

Scheduled events remain distinct from published NewsItem objects.

## TradeOpportunity

Broker-independent directional thesis.

```python
class TradeOpportunity(BaseModel):
    opportunity_id: str
    snapshot_id: str
    created_at: datetime
    ticker: str
    direction: Direction
    horizon: TradingHorizon
    expected_holding_min_days: int | None
    expected_holding_max_days: int | None
    confidence: float
    target_exposure_eur: float | None
    max_intended_loss_eur: float | None
    thesis: str
    key_risks: list[str]
    evidence_ids: list[str]
```

Validation:

```text
0 <= confidence <= 1
min holding <= max holding
```

A TradeOpportunity intentionally contains no Fineco-specific product.

## InstrumentCandidate

```python
class InstrumentCandidate(BaseModel):
    instrument_id: str
    opportunity_id: str
    eligible: bool
    rejection_reason: str | None
    estimated_margin_eur: float | None
    estimated_cost_eur: float | None
    suitability_score: float | None
```

Rejected candidates are retained for auditability.

If `eligible == False`, a rejection reason is required.

## TradeProposal

Concrete operator-ready proposal.

```python
class TradeProposal(BaseModel):
    proposal_id: str
    opportunity_id: str
    snapshot_id: str
    created_at: datetime
    ticker: str
    direction: Direction
    instrument_id: str
    quantity: float
    reference_price: float
    currency: Currency
    entry_type: str
    entry_price: float | None
    stop_price: float | None
    target_1: float | None
    target_2: float | None
    gross_exposure_eur: float
    estimated_margin_eur: float | None
    estimated_max_loss_eur: float | None
    expected_holding_min_days: int | None
    expected_holding_max_days: int | None
    status: ProposalStatus = PROPOSED
```

Direction is explicit and quantity is always positive:

```text
direction = SHORT
quantity = 20
```

not `quantity = -20`.

Creating a proposal does not imply broker execution.

## PortfolioRiskState

Compact deterministic state used for simulation comparisons.

```python
class PortfolioRiskState(BaseModel):
    gross_exposure_eur: float
    net_exposure_eur: float
    portfolio_volatility_pct: float
    portfolio_beta: float
    var_95_1d_eur: float
    cvar_95_1d_eur: float
    top5_concentration_pct: float
    effective_positions: float
```

## PortfolioSimulation

```python
class PortfolioSimulation(BaseModel):
    simulation_id: str
    snapshot_id: str
    proposal_id: str
    created_at: datetime
    before: PortfolioRiskState
    after: PortfolioRiskState
    delta: PortfolioRiskState
    cash_after_eur: float | None
    cash_after_usd: float | None
    constraints_passed: bool
    violated_constraints: list[str]
```

Semantics:

```text
BEFORE = current real snapshot
AFTER  = hypothetical portfolio after proposal
DELTA  = AFTER - BEFORE
```

AFTER never becomes REAL until a later Fineco snapshot confirms the portfolio.

## DecisionEvidence

```python
class DecisionEvidence(BaseModel):
    evidence_id: str
    snapshot_id: str
    created_at: datetime
    ticker: str | None
    quantitative_summary: str
    technical_summary: str | None
    news_ids: list[str]
    event_ids: list[str]
    portfolio_fit_summary: str | None
    instrument_rationale: str | None
    risk_constraints_summary: str | None
    model_name: str | None
    prompt_version: str | None
```

This contract preserves the answer to:

> Why did the CIO make this recommendation?

## CioDecision

```python
class CioDecision(BaseModel):
    decision_id: str
    proposal_id: str
    created_at: datetime
    decision: CioDecisionType
    confidence: float
    rationale: str
    evidence_ids: list[str]
    simulation_id: str
    warnings: list[str]
```

`ACCEPT` means CIO recommendation, not execution.

## Contract flow

```text
PortfolioSnapshot
       |
       +------ AccountState
       |
       v
Stage 2.5 Quant State
       |
       +------ NewsItem[]
       +------ MarketEvent[]
       |
       v
TradeOpportunity
       |
       v
FinecoInstrument[]
       |
       v
InstrumentCandidate[]
       |
       v
TradeProposal
       |
       v
PortfolioSimulation
       |
       v
CioDecision
       |
       +------ DecisionEvidence[]
       |
       v
Human Operator
       |
       v
Manual Fineco Execution
       |
       v
New PortfolioSnapshot
```

## Time handling

Persist timezone-aware datetimes.

Preferred persistence representation is UTC ISO 8601.

Example:

```text
2026-08-17T07:15:00Z
```

User-facing reporting may convert to local timezone.

## IDs

Recommended prefixes:

```text
ACC-   AccountState
SNAP-  PortfolioSnapshot
FIN-   FinecoInstrument
NEWS-  NewsItem
EVT-   MarketEvent
EVD-   DecisionEvidence
OPP-   TradeOpportunity
SIM-   PortfolioSimulation
CIO-   TradeProposal
DEC-   CioDecision
```

## LLM structured-output boundary

LLM-generated structures must pass Pydantic validation before entering deterministic workflow or persistent storage.

Financially material invalid output is rejected rather than silently repaired.

## Risk boundary

Deterministic constraints override AI preference.

A high-confidence CIO opportunity cannot bypass:

- cash constraints;
- maximum intended loss;
- exposure constraints;
- beta / VaR constraints;
- instrument eligibility.

The resulting CIO decision must be MODIFY or REJECT when deterministic limits fail.

## SQLite persistence

Initial Stage 3.0 SQLite entities:

```text
account_states
portfolio_snapshots
fineco_instruments
```

Planned:

```text
news_items
market_events
decision_evidence
trade_opportunities
instrument_candidates
trade_proposals
portfolio_simulations
cio_decisions
```

SQLite is not a broker ledger.

## Implementation order

1. Canonical enums.
2. Pydantic contracts.
3. Contract tests.
4. Account State persistence.
5. Fineco Instrument Cache persistence.
6. Portfolio Snapshot / SHA-256.
7. Market Intelligence contracts and providers.
8. Opportunity and proposal workflow.
9. Portfolio simulator.
10. Decision evidence.
11. LLM structured-output adapters.
12. CIO orchestration.

## Final rule

```text
FACTS
  -> DETERMINISTIC CALCULATIONS
  -> AI INTERPRETATION
  -> CIO PROPOSAL
  -> HUMAN DECISION
  -> MANUAL FINECO EXECUTION
  -> NEW FINECO SNAPSHOT
```

No layer may silently assume responsibility belonging to another layer.
