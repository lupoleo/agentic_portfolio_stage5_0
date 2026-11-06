# E2E-S2.2F — Scanner-to-Research Integration

## Status

- Contract approved: 2026-09-24
- Implementation status: CLOSED
- Policy: `stage4-v1-scanner-research-integration`
- Policy version: `1`
- Parent checkpoint: E2E-S2.2E

## Purpose

S2.2F consumes the immutable Research Watch Universe produced by S2.2E and
connects it to the frozen Research and Opportunity Scoring contracts. It does
not alter AI-8C.2 or AI-8C.3.

The integration preserves the Watch Universe fingerprint, research subject,
listing and portfolio provenance, evidence sources, inference identifiers,
research, score and any resulting `TradeOpportunity`.

## Directional hypotheses

The Watch Universe does not assert a trading direction. The adapter therefore
does not silently choose one.

| Watch provenance | Generated hypotheses |
| --- | --- |
| `NEW_CANDIDATE` | `NEW_LONG` and `NEW_SHORT` |
| `CURRENT_POSITION` | `PORTFOLIO_MONITOR` / `NO_ACTION` |
| Both | All three, retaining both provenance paths |

Portfolio monitoring produces research but cannot automatically materialize a
new opportunity. LONG and SHORT are independent competing hypotheses.

Members that are not `READY`, or that do not have exactly one canonical Yahoo
symbol, do not produce a `ScanCandidate`. They receive a deterministic,
persisted exclusion outcome.

## Pipeline

For each accepted hypothesis the integration:

1. persists a deterministic `MarketScan` and `ScanCandidate`;
2. requests market and news evidence;
3. aggregates and deduplicates evidence canonically;
4. persists the complete evidence bundle and source provenance;
5. invokes and persists frozen `OpportunityResearch`;
6. invokes and persists frozen `OpportunityScore`;
7. applies the deterministic opportunity materialization gate;
8. optionally persists `TradeOpportunity` plus a separate provenance link.

Evidence failure, incomplete research or a non-scorable result does not erase
the hypothesis. It produces an explicit outcome and reason.

## Opportunity materialization gate

A directional hypothesis creates a `TradeOpportunity` only when all conditions
hold:

- research is `COMPLETE`;
- additional research is not required;
- evidence quality is not `LOW`;
- all five opportunity components are scorable;
- confidence-adjusted score is at least `60`;
- score confidence is at least `0.40`;
- the appropriate bull or bear directional case is present.

The initial opportunity is broker-independent, has status `DISCOVERED`, uses
the canonical Yahoo ticker and V1 `SWING` horizon, and contains no position
sizing or selected Fineco instrument.

## Persistence

Existing Stage 3 persistence remains authoritative for:

- `MarketScan`;
- `ScanCandidate`;
- AI inference records;
- `OpportunityResearch`;
- `TradeOpportunity`.

Integration-specific SQLite tables persist:

- `ScannerResearchRun`;
- `ResearchHypothesisOutcome`;
- `PersistedEvidenceBundle`;
- `OpportunityScore`;
- `TradeOpportunityProvenanceLink`.

The provenance link avoids changing the frozen `TradeOpportunity` contract and
records the Watch Universe, subject, hypothesis, candidate, research, score,
evidence, inference and integration-policy identities.

## Resume and replay

Run, scan and hypothesis identifiers are derived from canonical input and
policy fingerprints. Completed research, opportunity and exclusion outcomes
are skipped on resume. Failed, degraded and cache-miss outcomes remain
retriable.

A bounded run may use `--max-hypotheses`; unprocessed hypotheses keep the run
`PARTIAL`. A later invocation without that limit resumes the same run.

`--cache-only` performs no provider or LLM calls. A cache miss is recorded as a
pending, retriable `CACHE_ONLY_MISS`, not as a permanent exclusion.

## Failure semantics

| Condition | Result |
| --- | --- |
| Member not ready | Explicit exclusion |
| Ambiguous/missing Yahoo identity | Explicit exclusion |
| No usable evidence | `DEGRADED`, retriable |
| Research failure | Member-local `FAILED`, retriable |
| Incomplete research | No opportunity |
| Partial or weak score | No opportunity |
| Provider rate limit exception | Run stops `RATE_LIMITED` and remains resumable |
| Zero qualifying opportunities | Valid completed run |

## Non-goals

S2.2F does not perform operator selection, Portfolio Filter, broker-instrument
selection, position sizing, simulation, CIO decision, execution planning or
portfolio mutation.

## Implementation files

- `app/scanner/research_integration_contracts.py`
- `app/scanner/research_integration.py`
- `app/scanner/research_integration_store.py`
- `app/scanner/research_integration_service.py`
- `tools/live_scanner_research_integration.py`
- `tests/test_scanner_research_integration.py`
- `tests/test_scanner_research_integration_store.py`
- `tests/test_scanner_research_integration_service.py`

## Closure evidence

S2.2F closed on 2026-09-24.

The accepted S2.2E Watch Universe contained 35 `READY` members and generated
37 deterministic hypotheses. Cache-only execution preserved run identity,
made no provider or LLM calls and persisted explicit, retriable cache misses.

The bounded live pilot validated both paths:

- `2BTC.DE` completed as research-only `PORTFOLIO_MONITOR` with
  `MONITOR_ONLY`;
- `A2A.MI NEW_LONG` and `A2A.MI NEW_SHORT` were independently researched;
- both directional hypotheses stopped fail-closed with
  `RESEARCH_NOT_COMPLETE`;
- zero `TradeOpportunity` records were created.

Resume validation used a deliberately invalid model name. Previously terminal
hypotheses were skipped, their persisted payloads and timestamps remained
unchanged, and no model call occurred.

The pilot also exposed and corrected a completion-accounting defect:
`PENDING`, `FAILED` and `DEGRADED` outcomes remain retriable and are not listed
in `completed_hypothesis_ids`.

Acceptance results:

- focused S2.2F regression: 12 passed;
- complete project regression: 1,503 passed;
- subtests: 162 passed;
- `git diff --check`: clean.

The full selected-opportunity dry run is the next proposed Stage 4.0
checkpoint. Its contract requires separate review and approval.
