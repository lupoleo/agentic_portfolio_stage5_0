# Stage 3.0 — AI-Assisted CIO Decision & Trade Simulator

## Status

Architecture specification built on the stable Stage 2.5 quantitative engine.

Stage 3.0 adds decision support, market intelligence, Fineco instrument selection and pre-trade simulation. It does **not** execute broker orders automatically.

## Core workflow

```text
Fineco Snapshot
    +
Account State (EUR / USD)
    +
Stage 2.5 Quantitative State
    +
Fineco Instrument Cache
    +
Market Intelligence
        |
        v
AI / CIO Decision Engine
        |
        v
Trade Opportunity
        |
        v
Instrument Selector
        |
        v
Trade Proposal
        |
        v
Portfolio Simulator
        |
        v
CIO Proposal for Operator
        |
        v
Human Review
        |
        v
Manual Fineco Execution
        |
        v
New Fineco Snapshot
```

## Source of truth

The latest imported Fineco Excel export is always the authoritative **REAL portfolio state**.

A proposed trade never mutates the real portfolio.

Three states remain separate:

- **REAL PORTFOLIO** — latest Fineco snapshot.
- **SIMULATED PORTFOLIO** — real portfolio plus hypothetical trade(s).
- **PROPOSED TRADE** — CIO recommendation not assumed to be executed.

After manual execution, the preferred synchronization mechanism is a new Fineco export followed by a complete Stage 2.5 re-analysis.

A new Fineco snapshot always overrides temporary local assumptions.

## Stage 2.5 — frozen quantitative engine

Stage 3.0 consumes Stage 2.5 outputs without duplicating their calculations:

- Technical analysis and scoring.
- LONG / SHORT-aware exposures.
- Concentration, HHI and effective positions.
- Covariance-aware portfolio volatility.
- Component risk contribution.
- Correlation analysis.
- Historical VaR / CVaR.
- Benchmark Beta / Alpha / R².
- Systematic / idiosyncratic risk.
- Multi-factor OLS diagnostics.

Python remains authoritative for financial calculations.

## Account State

Initial currencies:

- EUR
- USD

Account State may include:

- available cash;
- reserve cash;
- broker buying power when known;
- maximum intended trade loss;
- concentration limits;
- exposure limits;
- beta / VaR limits.

Account information may initially be supplied by the operator in natural language and normalized into validated structured data.

A proposed order does not change real Account State.

## Fineco Instrument Cache

Fineco product availability is underlying-specific and must never be inferred.

The cache may contain:

- ordinary shares;
- margin products;
- CFD / CFDC;
- certificates;
- intraday / overnight availability;
- LONG / SHORT availability;
- leverage;
- margin requirement;
- currency;
- known costs;
- last-confirmed timestamp;
- source;
- freshness status.

If the CIO needs an instrument that is absent or stale, it asks the operator. An LLM may parse the response, but financially material extracted values must be validated before persistent storage.

`UNKNOWN` is a valid state.

## Market Intelligence

Initial provider architecture is interchangeable.

Potential initial sources:

- Finnhub;
- Alpha Vantage;
- SEC / EDGAR;
- company Investor Relations;
- central banks;
- official macroeconomic sources.

Raw news is normalized, deduplicated, ticker-mapped and classified before reaching the CIO.

The CIO must preserve source identity and evidence references.

## Events

Upcoming events are distinct from published news.

Examples:

- earnings;
- CPI;
- FOMC;
- ECB;
- jobs data;
- dividends;
- regulatory decisions;
- company events.

Event timing is material to the trading horizon.

## Trading horizons

Initial horizons:

- `INTRADAY` — same trading session.
- `SWING` — approximately 2–10 trading days.
- `TACTICAL` — approximately 2–6 weeks.

Different horizons may use different evidence weights. There is no universal CIO trading score.

## CIO Decision Engine

The CIO performs five conceptual tasks:

1. **Diagnose** — identify portfolio strengths, weaknesses and exposures.
2. **Prioritize** — rank risks and opportunities.
3. **Find Opportunities** — produce directional LONG / SHORT theses.
4. **Evidence & Rationale** — bind the thesis to quantitative, news and event evidence.
5. **Decision** — accept, modify or reject a simulated proposal.

The CIO first produces a broker-independent `TradeOpportunity`.

## Instrument Selector

Input:

- TradeOpportunity;
- Fineco Instrument Cache;
- Account State.

Selection criteria may include:

- direction support;
- holding horizon;
- leverage;
- margin;
- cash;
- spread;
- commissions;
- financing cost;
- FX;
- maximum intended loss;
- portfolio constraints.

Rejected candidates are retained for auditability.

## Trade Proposal

A TradeProposal turns the selected instrument into an operator-ready structure:

- ticker;
- direction;
- Fineco instrument;
- quantity;
- reference price;
- entry type / price;
- stop;
- target(s);
- gross exposure;
- estimated margin;
- estimated maximum loss;
- holding horizon.

Initial status is always `PROPOSED`.

## Portfolio Simulator

Every proposal is simulated before final CIO recommendation.

```text
CURRENT PORTFOLIO
        +
PROPOSED TRADE
        =
SIMULATED PORTFOLIO
```

The simulator compares before / after values such as:

- gross exposure;
- net exposure;
- cash;
- concentration;
- portfolio volatility;
- beta;
- VaR / CVaR;
- factor exposure;
- risk contribution.

Stage 2.5 calculations should be reused wherever applicable.

The simulator never mutates the real portfolio.

## Operator and execution

The human operator remains permanently in control.

The operator:

- reviews proposals;
- accepts / modifies / rejects;
- manually executes accepted orders on Fineco;
- downloads an updated Fineco portfolio;
- supplies updated cash information when required.

Automatic broker execution is explicitly outside Stage 3.0.

## Snapshots and auditability

Every decision is bound to a `PortfolioSnapshot`.

Each snapshot records:

- unique snapshot ID;
- timezone-aware timestamp;
- Fineco source file;
- SHA-256 file hash;
- Stage 2.5 engine version;
- exposure summary;
- optional Account State reference.

Every opportunity, proposal, simulation and decision must retain its originating `snapshot_id`.

Decision evidence records the facts used by the CIO so that the system can later answer:

> Why did the CIO recommend this trade?

## Persistence

Stage 3.0 uses local SQLite for structured state.

Initial persisted entities:

- AccountState;
- PortfolioSnapshot;
- FinecoInstrument.

Later entities:

- NewsItem;
- MarketEvent;
- DecisionEvidence;
- TradeOpportunity;
- InstrumentCandidate;
- TradeProposal;
- PortfolioSimulation;
- CioDecision.

SQLite is an analytical state store, **not a broker ledger**.

## LLM boundary

The LLM may:

- interpret quantitative results;
- classify / summarize news;
- parse operator natural-language input;
- identify opportunities;
- compare alternatives;
- explain rationale.

The LLM must not:

- calculate authoritative portfolio metrics;
- invent Fineco instruments;
- invent costs;
- fabricate news;
- assume an order was executed;
- mutate the real portfolio;
- override deterministic risk constraints.

## Stage 3.0 MVP

The MVP must demonstrate one complete controlled cycle:

```text
Fineco Snapshot
+
Cash EUR/USD
+
Stage 2.5 Analysis
+
Market Intelligence
    ->
CIO Opportunity Detection
    ->
Fineco Cache Lookup
    ->
Operator clarification if required
    ->
Instrument Selection
    ->
Portfolio Simulation
    ->
CIO Trade Proposal
    ->
Console / Excel CIO Report
    ->
STOP
```

No automatic execution.

## Design principles

- Human stays in control.
- Fineco remains source of truth.
- Python calculates; AI interprets.
- Proposed != executed.
- Every trade is simulated before recommendation.
- UNKNOWN is better than fabricated data.
- Every decision is traceable to evidence and a portfolio snapshot.
- Architecture remains modular and provider-independent.

## Foundation CLI

The Stage 3.0 foundation includes a small local CLI for the first persistent state objects.

Default SQLite database:

```text
data/state/portfolio_cio.db
```

Commands:

```bash
python -m app.cio.cli account show
python -m app.cio.cli account set

python -m app.cio.cli instruments show GOOGL
python -m app.cio.cli instruments list
python -m app.cio.cli instruments add
```

`account set` interactively records current EUR / USD liquidity, reserves and optional risk constraints.

`instruments add` interactively records one Fineco instrument. Unknown fields can be left blank or explicitly marked unknown. This deterministic CLI is the foundation that the later natural-language LLM parser will write into only after validation.

The local SQLite state database remains excluded from Git and must never be treated as the broker's authoritative portfolio ledger.
