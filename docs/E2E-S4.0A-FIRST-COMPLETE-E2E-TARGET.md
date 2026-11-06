# E2E-S4.0A — First Complete End-to-End Target

Status: `APPROVED / IN PROGRESS`

## Purpose

E2E-S4.0A is an integration and acceptance layer. It connects the current
Fineco portfolio, Portfolio Analysis, Scanner, Research, explicit operator
selection and the complete downstream dry-run lifecycle without redefining
their business semantics.

## Contract freeze

The contracts closed by S2.2A–S2.2H, Portfolio Filter PF-1 and the downstream
instrument, sizing, proposal, simulation, CIO decision and ExecutionPlan
modules remain normative and frozen. The orchestrator consumes their public
objects and preserves IDs, fingerprints, reason codes and provenance.

Any representation mismatch must be handled by an adapter inside `app/e2e`.
If an incompatibility cannot be resolved without changing an existing
contract, the run stops and the proposed contract change requires separate
operator approval.

## Canonical flow

1. Persist a fresh PortfolioSnapshot and PortfolioRiskState from the current
   Fineco workbook and bind an explicit AccountState.
2. Execute scanner discovery, eligibility, mapping, availability, history and
   liquidity gates.
3. Assemble the immutable Candidate Set, Portfolio Watch Set and Research
   Watch Universe.
4. Run the persisted Scanner-to-Research integration and materialize zero or
   more TradeOpportunities.
5. Require the operator to select exactly one opportunity. No selection may be
   inferred from ranking; no batch allocation is permitted.
6. Run the selected opportunity through Portfolio Filter, Instrument
   Selection, Position Sizing, Trade Proposal, Portfolio Simulator V2, CIO
   Decision and ExecutionPlan.
7. Persist a single Stage4E2ERun manifest containing the complete lineage and
   safety evidence.

## Terminal outcomes

`COMPLETED`, `WAITING_FOR_OPERATOR_SELECTION`, `BLOCKED`, `PARTIAL` and
`FAILED` are explicit persisted outcomes. A real-current-data run is valid
when it stops fail-closed with a correct reason; it must never fabricate an
opportunity to reach completion.

## Persistence and replay

The run ID is deterministic for the immutable request identity and independent
of provider mode. Stage records are terminal and append-safe. A terminal run
is immutable, and a resume starts at the first incomplete boundary without
repeating completed network activity. `CACHE_ONLY` performs zero network calls.

## Safety invariants

Every path must preserve:

```text
dry_run = true
execution_authorized = false
broker_orders_submitted = 0
portfolio_mutations = 0
automatic_executions = 0
```

An ExecutionPlan may be produced only for manual review. No broker integration
or automatic execution is authorized.

## Acceptance tracks

The real-current-data track uses the current Fineco workbook, account state,
provider/cache evidence and persisted lineage. The controlled complete-path
track uses deterministic provider evidence, an explicit operator-selection
artifact and the real business services to traverse every downstream stage.

Closure requires focused contract, persistence, lineage, selection, cache,
resume and safety tests; both acceptance tracks; complete regression; and a
same-change-set update of `STAGE_4_0_ARCHITECTURE.md`.

## Publication intent

After closure, this contract and its evidence will form the normative source
for the Stage 4.0 Engineering Wiki published on the Aleph Innovation website.
The public documentation must be derived from the versioned implementation and
acceptance evidence rather than maintained as a separate architecture.

## Non-goals

This checkpoint does not change scanner rules, risk formulas, scoring,
portfolio constraints, account limits or downstream decisions. It does not
introduce automatic ranking, multi-opportunity allocation, broker execution or
portfolio mutation.

## Implementation progress — block 2

The integration layer now includes a resumable orchestrator and explicit thin
adapters for the frozen portfolio-analysis, scanner, watch-universe, research
and S2.2G services. The adapter boundary returns only canonical stage evidence;
it does not reinterpret domain decisions.

The service validates portfolio snapshot, watch-universe, research-run,
opportunity and ExecutionPlan lineage. Cache-only misses remain resumable with
zero network calls, while true terminal results are immutable. Any mismatched
identity or non-zero execution side effect stops fail-closed.

## Implementation progress — block 3

The real runtime binds the orchestrator to the existing Stage 3, S2.2E,
S2.2F and S2.2G services. All IDs, files, reports and as-of values are
explicit. No opportunity is selected implicitly; non-complete outcomes
persist their exact reason. Safety remains zero broker orders, portfolio
mutations and automatic executions.

## Corrective sub-checkpoint: E2E-S4.0A.1

The real A2A and SAP acceptance path produced four symmetric directional
outcomes, all correctly excluded with `RESEARCH_NOT_COMPLETE`. This proved the
research gates but exposed the absence of a bounded candidate-replenishment
loop. E2E-S4.0A.1 adds that orchestration above the frozen S2.2 contracts,
without relaxing evidence, scoring, safety or manual-execution policy.

## Corrective integration checkpoints

E2E-S4.0A.1 supplies bounded candidate replenishment when a valid upstream
run produces no selectable opportunity. E2E-S4.0A.2 supplies a news-sensitive,
persisted acquisition order for that frontier and corrects the shared
historical-as-of clock used by Yahoo history and ECB FX conversion.

Neither checkpoint selects an opportunity or relaxes an investment gate. They
only determine which admissible listing is examined next and preserve the
existing explicit operator-selection boundary.

## Research evidence completion bridge

E2E-S4.0A.3 supplies the previously absent technical, fundamental and analyst
evidence before Research inference. It reuses the canonical technical object
in Opportunity Scoring and preserves every existing completeness and
materialization threshold.

S4.0A.3 does not select an opportunity or relax an investment gate. It makes
already-required evidence dimensions available while preserving the explicit
operator-selection boundary.

The first isolated LIVE bridge pilot confirmed five-kind provider coverage
and exposed a narrower representation gap: explicit analyst price-target
evidence was not routed into the Expectations presence repair. The corrective
bridge adds that conditional routing and unit-aware fundamental values while
leaving every completeness and opportunity threshold unchanged.

The first fresh replay of that correction also proved the fail-closed status
boundary: an expectations-only repair attempted to promote research to
`COMPLETE` while material unknowns and an explicit additional-research flag
remained. The validators rejected it. The presence repair is therefore scoped
to `expectations_assessment`; research governance remains protected unless a
separate deterministic validator explicitly authorizes those fields.

S4.0A.3 subsequently closed with a fresh empty-database replay. CAG supplied
MARKET, TECHNICAL, FUNDAMENTAL and ANALYST evidence; all four evidence IDs were
cited, all conditionally required contexts were present after the scoped
Expectations repair, and no coverage warning remained. Its `0.5` semantic
coverage score correctly represented three of six canonical dimensions rather
than an adapter gap. The result remained `EXCLUDED / RESEARCH_NOT_COMPLETE`
because no current catalyst, macro or news context supported completion. The
full regression passed 1,620 tests and 162 subtests. E2E-S4.0A remains active:
bounded replenishment must now continue to a naturally selectable opportunity.
