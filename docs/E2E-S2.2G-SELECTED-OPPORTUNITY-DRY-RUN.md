# E2E-S2.2G — Full Selected-Opportunity Dry Run

## Status

`CLOSED`

The contract was approved on 2026-09-24 and closed on 2026-09-25 after focused tests, complete regression, real-data replay and controlled acceptance.

## Purpose

S2.2G selects at most one persisted S2.2F `TradeOpportunity` and coordinates
the already-frozen downstream lifecycle:

1. selection and S2.2F provenance validation;
2. explicit operator input overlay;
3. Portfolio Filter;
4. Portfolio Lifecycle Gate;
5. Instrument Selection;
6. Position Sizing;
7. Trade Proposal;
8. Portfolio Simulator V2;
9. CIO Decision;
10. optional manual Execution Plan.

It does not change the policy of any downstream component.

## Explicit operator overlay

S2.2F deliberately materializes broker-independent opportunities with
`target_exposure_eur=None` and `max_intended_loss_eur=None`. The frozen
Portfolio Filter and Position Sizer cannot run without exposure and market
inputs.

The approved S2.2G boundary therefore requires an explicit, persisted and
fingerprinted overlay containing:

- requested exposure in EUR;
- maximum intended loss in EUR;
- reference price;
- FX-to-EUR rate;
- stop price;
- market observation timestamp;
- optional instrument ID, entry and targets.

No default is inferred. The observation may not be newer than `as_of`. If the
opportunity already contains a conflicting exposure or loss limit, the run
blocks with `OPERATOR_INPUT_CONFLICT` rather than overwriting it silently.

## Selection contract

An executable dry run requires exactly one opportunity. The selected ID must:

- belong to the supplied `ScannerResearchRun.opportunity_ids`;
- have exactly one S2.2F `OPPORTUNITY_CREATED` outcome;
- originate from `NEW_LONG` or `NEW_SHORT`, never `PORTFOLIO_MONITOR`;
- have a complete `TradeOpportunityProvenanceLink`;
- exist in `Stage3Store`;
- use the same current `PortfolioSnapshot` as the S2.2F run and request;
- have been created no later than the dry-run `as_of`.

Zero opportunities are representable for an honest real-data replay and
produce `NO_SELECTABLE_OPPORTUNITY`. More than one selection is rejected by
the request contract.

## Run semantics

The deterministic run identity excludes only `LIVE` versus `CACHE_ONLY`, so a
cache miss can resume with the same business inputs. Completed stages are
immutable and skipped on resume. A business-policy block is terminal and does
not call downstream stages. A failed stage remains retryable while already
completed stages are preserved.

The orchestration store records:

- request and policy fingerprint;
- S2.2F run, opportunity and portfolio snapshot IDs;
- ordered stage records;
- input and output IDs and fingerprints;
- reason codes and diagnostics;
- final optional execution-plan ID;
- zero side-effect counters.

## Execution boundary

An Execution Plan is created only after a CIO `ACCEPT`. It remains in the
existing manual confirmation lifecycle and receives an explicit `DRY RUN
ONLY — NOT AUTHORIZED FOR BROKER EXECUTION` note.

Every S2.2G run enforces:

```text
dry_run = true
execution_authorized = false
broker_orders_submitted = 0
portfolio_mutations = 0
automatic_executions = 0
```

No broker adapter or credential is used by the checkpoint.

## Valid terminal outcomes

The run may complete with `DRY_RUN_PLAN_CREATED` or block explicitly at an
earlier policy boundary, including:

- `NO_SELECTABLE_OPPORTUNITY`;
- `INVALID_SELECTION`;
- `PORTFOLIO_SNAPSHOT_MISMATCH`;
- `STALE_PORTFOLIO_SNAPSHOT`;
- `PORTFOLIO_FILTER_BLOCKED`;
- `NO_ELIGIBLE_INSTRUMENT`;
- `MARKET_INPUT_UNAVAILABLE`;
- `POSITION_SIZING_BLOCKED`;
- `CIO_REJECTED`;
- `CIO_MODIFY_REQUIRED`.

A legitimate rejection is not reported as an infrastructure failure.

## Closure evidence

The focused S2.2G suite passed 15 tests. The complete project regression
passed 1,518 tests and 162 subtests.

The real replay of S2.2F run `s2f-444373de3ccd1e6f41439998` correctly
terminated with `NO_SELECTABLE_OPPORTUNITY`. Its S2.2G run ID was
`s2g-6d56888ea50a391b9a7b0dc0`.

The controlled ten-stage acceptance created a dry-run Execution Plan and
proved terminal replay immutability. All evidence recorded
`execution_authorized=false`, zero broker orders, zero portfolio mutations
and zero automatic executions.

## Non-goals

S2.2G does not implement automatic opportunity ranking, multi-opportunity
allocation, broker submission, operator confirmation, post-trade
reconciliation or any change to frozen quantitative and CIO policies.
