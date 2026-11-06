# Agentic Portfolio - CIO Demo Roadmap

## Purpose of this demo

This demo shows the current working path from a real brokerage portfolio
to a structured CIO trade proposal and portfolio-risk simulation.

The important point is that the system is deliberately split into two
layers:

1.  **Deterministic quantitative computation** - portfolio mathematics,
    exposures, risk metrics, instrument eligibility, position sizing,
    persistence and lifecycle controls are handled locally by Python
    code.
2.  **AI / agentic intelligence** - the next layer will interpret the
    quantitative state, incorporate qualitative information, challenge
    assumptions, explain alternatives and support the CIO decision.

This separation is intentional. AI is not being used to calculate
numbers that can be calculated deterministically and audited. It will be
used where reasoning, interpretation, synthesis and decision support add
value.

The current version therefore demonstrates the **quantitative and
transactional foundation on which the AI CIO will operate**.

------------------------------------------------------------------------

# 1. Starting point: the real Fineco portfolio

The current Fineco portfolio workbook is stored at:

``` text
data/input/portafoglio-export.xlsx
```

The workbook contains the brokerage portfolio and is used as the source
for the quantitative portfolio analysis.

The analysis also writes additional analytical worksheets into the same
workbook.

## Run the portfolio analysis

From the project root:

``` powershell
python -m app.analysis.portfolio
```

## What this step does

The Portfolio Analysis / Quant Engine:

-   imports the current Fineco portfolio;
-   resolves Fineco instruments to market-data symbols where possible;
-   downloads market history;
-   calculates technical and portfolio analytics;
-   evaluates portfolio exposure and concentration;
-   calculates covariance-based risk measures;
-   calculates benchmark and factor-related analytics;
-   calculates historical VaR / CVaR and tail-risk measures;
-   performs market-price quality checks;
-   writes analytical sheets back into the Excel workbook;
-   persists a canonical `PortfolioSnapshot`;
-   persists the associated `PortfolioRiskState`.

The output should finish with something similar to:

``` text
=== SUMMARY ===
Analyzed positions: 33
Gross exposure:     EUR 371,343.34
Total weight:       98.74%

=== STAGE 3 CIO STATE PERSISTED ===

PortfolioSnapshot: SNAP-...
PortfolioRiskState: RISK-...
Analytical coverage: 95.45%

Excel analysis written to: data\input\portafoglio-export.xlsx
```

### How to explain this during the demo

The **PortfolioSnapshot** is the frozen portfolio state used by the CIO
workflow.

The **PortfolioRiskState** contains the quantitative risk representation
associated with exactly that snapshot: exposure, volatility, beta, VaR,
CVaR, concentration, effective number of positions and analytical
coverage.

This is important because every subsequent CIO action can be traced back
to the portfolio state on which the decision was based.

------------------------------------------------------------------------

# 2. Optional verification of the persisted portfolio state

The latest snapshot can be checked directly:

``` powershell
python -c "from app.cio.storage import Stage3Store; s=Stage3Store('data/state/portfolio_cio.db'); x=s.get_latest_portfolio_snapshot(); print(x)"
```

The associated risk state can be checked with:

``` powershell
python -c "from app.cio.storage import Stage3Store; s=Stage3Store('data/state/portfolio_cio.db'); x=s.get_latest_portfolio_snapshot(); r=s.get_latest_portfolio_risk_state(x.snapshot_id); print(r)"
```

These commands are mainly technical verification steps and do not need
to be central to the business demo.

------------------------------------------------------------------------

# 3. Create a new CIO Trade Opportunity

For the demo, create a **new SMCI SHORT opportunity**, but make it
slightly different from the previous test so that it is visibly a new
scenario.

Run:

``` powershell
python -m app.cio.cli opportunities add
```

The command is interactive.

## Suggested demo inputs

### Ticker

``` text
SMCI
```

The underlying security: Super Micro Computer.

### Direction

``` text
SHORT
```

A SHORT opportunity expresses the expectation that the underlying price
may decline.

### Trading horizon

``` text
SWING
```

**SWING** means a position intended to exploit a directional move over
several days or potentially a few weeks.

Other supported concepts include:

-   **INTRADAY** - opened and closed during the same trading session.
-   **TACTICAL** - a shorter-term opportunistic trade, often driven by a
    specific market condition or catalyst.
-   **SWING** - a multi-session directional trade, normally held for
    days or weeks.

### Confidence

For the demo use:

``` text
75
```

This means a **75% opportunity confidence score**.

It is not a mathematical probability of profit. It is a structured
expression of conviction that can later be produced or challenged by the
AI CIO.

### Expected holding minimum days

``` text
4
```

### Expected holding maximum days

``` text
12
```

This creates a slightly different scenario from the previously tested
5-15 day SMCI opportunity.

### Thesis

Suggested demo text:

``` text
Bearish swing thesis on SMCI; expecting downside over the next 4-12 days.
```

The **thesis** explains *why* the opportunity exists.

In the future this field can become much richer and can be generated,
challenged and updated by the AI layer using quantitative signals,
fundamentals, news and market context.

### Catalyst

For this demo it can be left blank by pressing Enter.

A **catalyst** is a specific event or condition that could trigger or
accelerate the price movement described by the thesis.

Examples include:

-   earnings;
-   guidance changes;
-   analyst upgrades or downgrades;
-   regulatory events;
-   major customer wins or losses;
-   macroeconomic releases;
-   central-bank decisions;
-   sector-specific news.

The distinction is useful:

> **Thesis = why the asset should move.**\
> **Catalyst = what may cause the move to happen now.**

### Snapshot ID

The CLI should automatically propose the latest persisted real portfolio
snapshot:

``` text
Snapshot ID [SNAP-...]:
```

Press **Enter** to accept it.

This is a key demo point: the opportunity is now explicitly attached to
the real portfolio state that was produced by the Quant Engine.

### Target exposure EUR

Use:

``` text
5000
```

This is the desired nominal exposure for the proposed trade.

It is **not** the maximum acceptable loss.

### Max intended loss EUR

Use:

``` text
6000
```

This is the maximum loss budget associated with the opportunity.

The system will eventually combine this with stop-loss information and
portfolio constraints to determine whether the proposed trade is
acceptable.

### Notes

Optional. For the demo:

``` text
TONI DEMO
```

The opportunity should then be persisted with a new ID similar to:

``` text
OPP-SMCI-...
```

Copy this ID because it will be used in the following commands.

------------------------------------------------------------------------

# 4. Inspect available Fineco instruments and run Instrument Selection

The CIO does not assume that an investment idea must be implemented
using the ordinary share.

Fineco may provide several instruments connected to the same underlying,
for example:

-   ordinary stock;
-   inverse ETF;
-   leveraged ETF;
-   CFD;
-   overnight margin product;
-   intraday leveraged product;
-   structured product.

The system therefore separates the **investment opportunity** from the
**instrument used to implement it**.

Run:

``` powershell
python -m app.cio.cli opportunities instruments <OPPORTUNITY_ID>
```

Example:

``` powershell
python -m app.cio.cli opportunities instruments OPP-SMCI-...
```

## What the Instrument Selector does

It evaluates the known Fineco instruments and ranks them according to
compatibility with:

-   LONG or SHORT direction;
-   trading horizon;
-   instrument characteristics;
-   leverage mode;
-   direct, inverse or leveraged relationship with the underlying.

For a SHORT / SWING opportunity, for example:

-   an ordinary share may be eligible if short selling is available;
-   an inverse ETF may be eligible;
-   an overnight CFD may be eligible;
-   an intraday-only leveraged product should be rejected because the
    opportunity is multi-session;
-   a leveraged-long product should be rejected because its direction
    conflicts with the SHORT thesis.

The output shows:

``` text
Rank
Eligible
Score
Type
Mode
Relation
Market
Broker leverage
Description
```

The opportunity should move to:

``` text
INSTRUMENTS_RANKED
```

### Business significance

This is already more than a conventional portfolio dashboard.

The system is starting to answer:

> "Given this investment idea, what instrument available at my actual
> broker is suitable for implementing it?"

------------------------------------------------------------------------

# 5. Optional: inspect persisted candidates

Run:

``` powershell
python -m app.cio.cli opportunities candidates <OPPORTUNITY_ID>
```

This shows that the ranking was persisted and can therefore be reused by
later CIO stages.

------------------------------------------------------------------------

# 6. Position Sizing

Run:

``` powershell
python -m app.cio.cli opportunities size <OPPORTUNITY_ID>
```

The system will normally use the top-ranked eligible instrument.

## Interactive parameters

### Reference price

Enter the current Fineco price of the selected instrument.

For example:

``` text
37.45
```

The reference price is the market price used to calculate the proposed
quantity and exposure.

### FX conversion

If the selected instrument is in USD, the CLI currently asks:

``` text
1 USD = how many EUR:
```

For the tested demo flow we used:

``` text
0.86
```

This FX input is currently manual.

### Future multicurrency improvement

Fineco supports multicurrency operation. A future version should
understand at least two execution models:

1.  use existing EUR liquidity and explicitly convert part of it into
    USD;
2.  submit the USD-denominated order while allowing the broker to
    perform the currency conversion automatically.

This is intentionally lower priority than completing the first full CIO
decision lifecycle.

### Stop price

For this demo, leave it blank by pressing Enter.

A future production trade should normally have an explicit
risk-management policy. When a stop price is available, the system can
estimate the monetary loss between entry and stop and compare it with
the trade-loss constraint.

Without a stop, the simulator correctly reports the maximum trade loss
as unknown rather than inventing a value.

### Override target exposure EUR

Leave this blank.

The Position Sizer will use the EUR 5,000 target already stored in the
Trade Opportunity.

## Expected result

The Position Sizer calculates an integer quantity that stays close to
the target exposure.

A successful result looks conceptually like:

``` text
Execution side:  SELL_SHORT
Quantity:        ...
Reference price: ... USD
Gross exposure:  approximately EUR 5,000
Risk budget:     EUR 6,000
Constraints:     PASS
```

The opportunity moves to:

``` text
POSITION_SIZED
```

------------------------------------------------------------------------

# 7. Create the Preliminary Trade Proposal

Run:

``` powershell
python -m app.cio.cli opportunities propose <OPPORTUNITY_ID>
```

## Entry type

Accept the default:

``` text
MARKET
```

A MARKET proposal means that no fixed execution price is specified.

The architecture can later support richer order strategies such as LIMIT
orders.

### Entry price

Leave blank for a MARKET proposal.

The Position Sizing reference price remains available as the valuation
reference, but it is not treated as a guaranteed execution price.

### Target 1

Leave blank for this demo.

### Target 2

Leave blank for this demo.

Targets are intentionally not invented simply to complete the form. In a
later CIO version they can be generated from technical levels, expected
return distributions, risk/reward constraints or AI-supported scenario
analysis.

## Expected result

A proposal similar to the following should be created and persisted:

``` text
Proposal ID:    PROP-SMCI-...
Opportunity ID: OPP-SMCI-...
Snapshot:       SNAP-...
Ticker:         SMCI
Direction:      SHORT
Execution side: SELL_SHORT
Instrument ID:  ...
Sizing ID:      ...
Quantity:       ...
Reference:      ... USD
Entry type:     MARKET
Gross exposure: approximately EUR 5,000
Holding:        4-12 days
Status:         PROPOSED
```

The opportunity moves to:

``` text
READY_FOR_PROPOSAL
```

At this stage **no broker order has been executed**.

The system has produced a structured, auditable trade proposal.

------------------------------------------------------------------------

# 8. Portfolio Risk Simulation

The current implementation also supports the next step, and it is useful
to show it if time allows.

Run:

``` powershell
python -m app.cio.cli opportunities simulate <OPPORTUNITY_ID>
```

This is now automatic.

The service resolves:

``` text
TradeProposal
      |
      v
PortfolioSnapshot
      |
      v
PortfolioRiskState
      |
      v
AccountState
      |
      v
PortfolioRiskSimulator
      |
      v
PortfolioSimulation
```

No portfolio-risk metrics need to be typed manually.

For a new SHORT position, the simulation should show the expected
exposure behaviour:

``` text
Gross exposure: increases
Net exposure:   decreases
Long exposure:  unchanged
Short exposure: increases
```

The simulation is hypothetical and does **not** modify the real
brokerage portfolio.

## Current V1 limitation

PortfolioRiskSimulator V1 changes exposure correctly but does not yet
recompute all quantitative risk measures after the hypothetical trade.

Therefore volatility, beta, VaR, CVaR, concentration and effective
positions are currently preserved from the BEFORE state and accompanied
by explicit warnings.

This is deliberate: the system reports what it knows and identifies what
it has not yet recomputed.

------------------------------------------------------------------------

# 9. Current architecture

The working architecture can be summarized as:

``` text
                FINECO PORTFOLIO
                       |
                       v
              PORTFOLIO IMPORT
                       |
                       v
             QUANTITATIVE ENGINE
                       |
          +------------+------------+
          |                         |
          v                         v
 PortfolioSnapshot          PortfolioRiskState
          |                         |
          +------------+------------+
                       |
                       v
                 STAGE 3 STORE
                       |
                       v
               TRADE OPPORTUNITY
                       |
                       v
              INSTRUMENT SELECTOR
                       |
                       v
                POSITION SIZER
                       |
                       v
                TRADE PROPOSAL
                       |
                       v
             PORTFOLIO SIMULATOR
                       |
                       v
              CIO DECISION ENGINE
                  [NEXT MODULE]
                       |
                       v
              MANUAL EXECUTION
                  AT BROKER
                       |
                       v
             NEW FINECO PORTFOLIO
                       |
                       +----> new analysis cycle
```

The architecture intentionally keeps **broker execution manual** at this
stage.

The CIO is a decision-support system, not an autonomous trading bot.

------------------------------------------------------------------------

# 10. Where AI fits into the architecture

One of the most important characteristics of the project is that **very
little AI is required for the foundation demonstrated today**.

This is a feature, not a limitation.

Portfolio arithmetic, exposure calculations, VaR, covariance, instrument
rules, position sizing and state transitions should be deterministic,
reproducible and testable.

AI becomes valuable on top of this trusted quantitative foundation.

## Potential AI roles

### Opportunity discovery

AI agents could continuously examine:

-   portfolio analytics;
-   technical signals;
-   company fundamentals;
-   earnings;
-   news;
-   macroeconomic developments;
-   sector developments;
-   analyst revisions;
-   market regime changes.

They could convert this information into candidate `TradeOpportunity`
objects.

### Investment thesis generation

Instead of manually entering:

``` text
Bearish swing thesis on SMCI...
```

the AI could produce a structured thesis explaining:

-   why the opportunity exists;
-   what evidence supports it;
-   what evidence contradicts it;
-   expected horizon;
-   relevant catalysts;
-   principal risks;
-   conditions that would invalidate the thesis.

### Thesis challenge / adversarial review

A second agent could deliberately argue against the opportunity.

For example:

> "What evidence would make this SMCI short a bad trade?"

This creates an internal investment-committee style debate rather than
simply asking an LLM for a BUY or SELL answer.

### News and catalyst interpretation

AI is particularly useful for unstructured information.

It can interpret earnings releases, management commentary, news articles
and macroeconomic developments and relate them to positions already
present in the portfolio.

### Instrument-selection explanation

The deterministic selector can rank instruments.

AI can explain *why* an ordinary share, inverse ETF or CFD may be
preferable in the current context, including qualitative considerations
not represented by the deterministic scoring model.

### Portfolio-aware decision support

The AI CIO should not evaluate a trade in isolation.

It can reason over the quantitative portfolio state:

-   existing sector concentration;
-   correlated positions;
-   factor exposure;
-   current drawdown;
-   liquidity;
-   portfolio beta;
-   VaR / CVaR;
-   existing long and short exposure.

The question becomes:

> "Is this a good trade **for this portfolio now**?"

rather than merely:

> "Is SMCI likely to fall?"

### CIO Decision Engine

This is the most important next AI-enabled module.

The Decision Engine will receive the complete structured evidence:

``` text
TradeOpportunity
Instrument ranking
PositionSizingResult
TradeProposal
PortfolioSnapshot
PortfolioRiskState
PortfolioSimulation
Market / news context
```

and produce a controlled decision such as:

``` text
ACCEPT
MODIFY
REJECT
```

with:

-   rationale;
-   confidence;
-   risk warnings;
-   proposed modifications;
-   invalidation conditions;
-   evidence references.

The deterministic constraint engine remains authoritative for hard risk
rules. AI does not get permission to override portfolio safety
constraints simply because it has a persuasive narrative.

------------------------------------------------------------------------

# 11. Development roadmap after this demo

The current milestone establishes the working foundation from real
portfolio data through a persisted Trade Proposal and Portfolio
Simulation.

The next development priorities are:

1.  **CIO Decision Engine**\
    Convert the complete opportunity, proposal and simulated portfolio
    state into an explicit `ACCEPT / MODIFY / REJECT` decision with
    rationale and audit trail.

2.  **PortfolioRiskSimulator V2**\
    Recompute the portfolio quantitatively after the hypothetical trade
    rather than preserving the existing volatility, beta, VaR, CVaR and
    concentration metrics.

3.  **Explicit trade-loss modelling**\
    Introduce stop-loss / invalidation logic and calculate
    `estimated_max_loss_eur` so that the maximum-loss constraint can be
    enforced quantitatively.

4.  **Canonical NAV / equity model**\
    Add a clear account-equity/NAV definition so that constraints such
    as maximum gross exposure as a percentage of portfolio equity can be
    evaluated reliably.

5.  **AI research and reasoning layer**\
    Add agents for opportunity discovery, thesis generation, catalyst
    detection, news interpretation, adversarial review and CIO decision
    support.

6.  **Market-data and news integration**\
    Add reliable feeds for prices, fundamentals, earnings, macro events
    and relevant news.

7.  **FX and multicurrency management**\
    Model EUR/USD liquidity, explicit currency conversion and
    broker-assisted currency conversion.

8.  **More sophisticated instrument modelling**\
    Extend support for CFDs, leveraged products, certificates, options
    and structured instruments, including their different payoff and
    risk characteristics.

9.  **Decision audit trail**\
    Preserve not only the final decision but the quantitative evidence,
    AI reasoning summary, warnings and portfolio state used to reach it.

10. **Manual execution confirmation loop**\
    The user executes an approved trade at the broker and explicitly
    confirms execution. A new Fineco portfolio is then imported,
    creating the next canonical snapshot and closing the feedback loop.

11. **Platform abstraction**\
    Continue separating the portfolio/CIO domain from Fineco-specific
    implementation so that the architecture can eventually connect to
    other regulated investment platforms.

------------------------------------------------------------------------

# Final message for the demo

The current system should not be presented as an AI that "predicts
stocks."

It is better described as the beginning of a **portfolio-aware digital
CIO architecture**.

The quantitative engine establishes a trusted representation of the real
portfolio. The CIO workflow converts an investment idea into a
broker-compatible instrument choice, position size, structured trade
proposal and simulated portfolio impact. The future AI layer will sit
above that foundation and provide the part machines are increasingly
good at: interpreting large amounts of structured and unstructured
information, challenging investment theses, identifying relevant context
and supporting explainable decisions.

The objective is therefore not to replace deterministic financial
computation with AI.

It is to combine **deterministic quantitative finance + auditable
workflow + AI reasoning** in a single decision-support architecture.
