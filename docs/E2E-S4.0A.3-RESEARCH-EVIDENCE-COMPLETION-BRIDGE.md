# E2E-S4.0A.3 — Research Evidence Completion Bridge

Status: `CLOSED`

## Purpose

Live S4.0A.1 and S4.0A.2 evidence proved that replenishment, multi-venue
frontier acquisition, history verification and transient retry work as
designed. They also proved that every successfully researched directional
hypothesis remained `RESEARCH_NOT_COMPLETE` because the live integration
supplied market and news evidence but not all of the semantic evidence needed
by the frozen Research and Opportunity Scoring contracts.

S4.0A.3 closes that adapter gap. It does not relax research completeness,
evidence quality, scoring confidence or opportunity materialization gates.

## Contract

The bridge adds three optional, provenance-preserving inputs to the existing
Scanner-to-Research integration:

1. `CANONICAL_TECHNICAL` converts the existing Stage-2 canonical technical
   input into `TECHNICAL` research evidence before inference. The identical
   immutable object is reused by Opportunity Scoring.
2. `YAHOO_FUNDAMENTAL` normalizes only available revenue, EPS, margin, cash
   flow, debt, liquidity and valuation fields into `FUNDAMENTAL` evidence.
3. `YAHOO_ANALYST` normalizes only available consensus, price-target,
   estimate and revision fields into `ANALYST` evidence.

The adapter and providers use the immutable hypothesis `as_of` clock. Each
item records its provider, source identity, source type, field coverage,
policy version and timestamps. Missing fields are omitted and never inferred.

## Failure and cache semantics

The two Yahoo enrichment providers are optional. A provider exception is
isolated as a persisted `PARTIAL` provider result with a diagnostic warning;
it cannot silently create evidence and does not erase evidence from other
providers. A missing or failed canonical technical input is handled the same
way and Opportunity Scoring receives no synthetic replacement.

`CACHE_ONLY` does not construct or invoke any live evidence provider and makes
zero network calls. Resume preserves terminal hypothesis outcomes and their
persisted evidence bundles.

## Frozen gates

S4.0A.3 does not modify:

- `ResearchStatus.COMPLETE` requirements;
- `requires_additional_research=false`;
- minimum evidence quality;
- five-component `OpportunityScore` requirements;
- minimum confidence-adjusted score;
- minimum score confidence;
- LONG/SHORT symmetry;
- Portfolio Filter, sizing, proposal, simulation, CIO or execution contracts.

A live result may therefore remain a valid non-complete outcome when source
coverage or model-grounded research is insufficient.

## Expectations-presence and numeric-grounding correction

The first isolated LIVE pilot persisted all five evidence kinds with every
provider successful, but Research still returned `UNKNOWN` for Expectations.
The analyst item already contained current price, price targets, consensus,
earnings estimates and revenue estimates. The missing value was therefore a
context-presence routing defect rather than an evidence-provider failure.

When analyst metadata proves that both current price and at least one explicit
price target exist, `expectations_assessment` now enters the existing targeted
repair boundary. The repair may select a priced-in enum only from those
supplied comparisons. When that basis is absent, `UNKNOWN` remains correct and
no repair is required.

The same pilot exposed a decimal-scale error in model prose: source debt of
7,475,100,160 was rendered as 74.75 billion. Fundamental monetary evidence now
includes a deterministic scaled representation and the exact raw units;
percentages include both display percentage and source decimal. This improves
grounding without changing any score or investment threshold.

The first fresh corrective replay reached that targeted repair and then
failed closed because the presence gate had also authorized the model to
rewrite `research_status` and `requires_additional_research`. The model
promoted the result to `COMPLETE` while retaining material unknowns and
requiring more research; the frozen validators rejected all three
contradictions. Expectations presence now authorizes only the missing
`expectations_assessment` field. Governance fields remain protected unless an
independent deterministic coverage or semantic error explicitly includes them
in the repair schema.

## Safety invariants

Every path preserves:

```text
allow_execution = false
broker_orders_submitted = 0
portfolio_mutations = 0
automatic_executions = 0
```

## Closure evidence

The focused S4.0A.3 governance suite passed 46 tests. After aligning the
pre-existing prompt-version assertions with the intentionally bumped
`opportunity-research-v1.3` contract, the complete project regression passed
1,620 tests and 162 subtests. `git diff --check` passed.

The fresh isolated LIVE replay used an empty database and persisted research
run `s2f-f35b2446c7c8aa144b0cfb63`. CAG `NEW_LONG` produced evidence bundle
`evidence-7d448e1bb5e87791edbcb170`, research
`RES-20260929-132023-e41ef2` and score
`score-03012d25a9246477e46e2e78`.

The live bundle contained the four evidence kinds actually available for CAG:
MARKET, TECHNICAL, FUNDAMENTAL and ANALYST. All four evidence IDs were cited.
The deterministic context-presence repair changed the initially missing
Expectations value to `PARTIALLY_PRICED_IN`; afterwards every required context
was present and the coverage validator reported no warnings.

The semantic coverage score remained correctly equal to `0.5`: three of the
six canonical dimensions were represented (`PRICE_TECHNICAL`, `FUNDAMENTAL`
and `ANALYST_EXPECTATIONS`), while current evidence did not establish
`CATALYST_EVENT`, `MACRO` or `NEWS_CONTEXT`. Research therefore remained
`PARTIAL` with `requires_additional_research=true`, and the hypothesis stopped
as `EXCLUDED / RESEARCH_NOT_COMPLETE` without fabricating a TradeOpportunity.
This is accepted live fail-closed behavior, not a bridge failure.

The controlled and regression evidence also proves that:

- deterministic technical evidence contains the canonical price, returns,
  SMA20, SMA50, RSI14, RVOL and trend values;
- fundamental and analyst providers emit only source-backed fields;
- all new evidence uses the immutable wave clock and persists provenance;
- a supported bundle contains MARKET, NEWS, TECHNICAL, FUNDAMENTAL and ANALYST
  kinds before Research inference;
- the technical object used by Research is the same object reused by Scoring;
- optional-provider failure is isolated and persisted fail-closed;
- CACHE_ONLY performs zero provider calls;
- a controlled source-complete case reaches `COMPLETE + SCORED` without any
  relaxed threshold;
- live source-incomplete evidence stops validly without an opportunity;
- all zero-side-effect invariants remain true.
