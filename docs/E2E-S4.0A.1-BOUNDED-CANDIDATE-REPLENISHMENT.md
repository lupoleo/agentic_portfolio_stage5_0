# E2E-S4.0A.1 — Bounded Candidate Replenishment

Status: `IMPLEMENTED / LIVE VALIDATION PENDING`

## Purpose

S4.0A originally stopped correctly when all directional candidates were
excluded by research and scoring. The real A2A and SAP run proved that this
fail-closed behavior is necessary, but also exposed the missing orchestration
feedback loop: zero selectable opportunities must be able to advance to an
unattempted scanner frontier without weakening any quality gate.

## Frozen policy

- preserve the existing S2.2A–S2.2H contracts;
- two listings per wave and symmetric LONG/SHORT hypotheses;
- at most five waves and one transient retry;
- never retry terminal investment-quality exclusions in the same session;
- CACHE_ONLY never makes network calls;
- LIVE may acquire verification and history for the selected frontier;
- portfolio monitors remain independent from directional replenishment;
- no threshold relaxation, batch allocation, broker order or portfolio mutation;
- every wave and child S4.0A run is append-only and attributable.

## Dispositions

- `RETRY_CURRENT`: one bounded retry for a transient processing failure;
- `ADVANCE_FRONTIER`: select the next canonical unattempted listing batch;
- `STOP_FAIL_CLOSED`: stop on opportunity, pending work, exhaustion, budget or
  safety failure.

## Terminal reasons

- `SELECTABLE_OPPORTUNITY_FOUND`;
- `PENDING_DIRECTIONAL_WORK`;
- `UNIVERSE_EXHAUSTED`;
- `REPLENISHMENT_BUDGET_EXHAUSTED`;
- `SAFETY_BLOCK`.

## Implementation blocks

Block 1 provides immutable contracts, deterministic frontier planning,
classification rules and an append-only SQLite wave ledger. Block 2 will wire
the planner to LIVE S2.2C/S2.2D acquisition and child S4.0A execution.

## Implementation progress — block 2

The bounded orchestration service now continues across canonical two-listing
waves until the first selectable `TradeOpportunity` is produced or an approved
safety terminal reason is reached. The service replays immutable wave evidence
on resume, never re-executes a persisted plan, retries a transient failure once
and preserves zero broker, portfolio and automatic-execution side effects.

The LIVE acquisition and child-run adapter remains the final implementation
block before real replenishment acceptance.

## Implementation progress — block 3

The canonical LIVE executor now acquires an S2.2D history report for each
two-listing wave, assembles a wave-specific S2.2E watch universe, runs an
S4.0A child lineage and reads its persisted S2.2F directional outcomes. The
outer service continues until the first selectable opportunity or an approved
bounded safety stop. CACHE_ONLY produces explicit misses with zero network
calls. LIVE provider failures receive exactly one retry. Explicit operator
selection remains mandatory before S2.2G downstream processing.

Implementation is complete; real replenishment acceptance and complete project
regression remain pending before closure.

## Block 3A — Acquisition-Capable Frontier

Live validation exposed a schema-boundary defect rather than a genuinely
exhausted Scanner universe. The canonical E2E-S2.2C mapping audit stores the
resolved Yahoo identity in `yahoo_symbol`; the initial replenishment planner
read only the compatibility name `resolved_symbol`. Consequently, the two
previously attempted listings appeared to exhaust an otherwise large eligible
frontier before any wave could start.

Block 3A reads the canonical field, retains the legacy alias only for backward
compatibility, rejects conflicting dual representations and persists explicit
frontier evidence. In LIVE mode, the selected frontier remains subject to the
existing S2.2D Yahoo history acquisition and every frozen history-quality and
liquidity gate. No eligibility, mapping, quality or scoring threshold is
relaxed.



## Implementation progress — Block 3B

Block 3B adds terminal review routing to the acquisition-capable frontier.
A completed history pilot may return exit code 2 when one or more listings
are deterministically `REVIEW_REQUIRED` or `BLOCKED`. Those listings are now
represented as symmetric `EXCLUDED / EVIDENCE_UNAVAILABLE` outcomes instead
of transient `PROCESSING_FAILED` outcomes. They do not consume the technical
retry and the bounded service advances to the next candidate wave.

Mixed waves retain their `STANDARD` listings for Scanner-to-Research while
terminal review listings receive explicit synthetic exclusions. Provider
exceptions, missing reports and incomplete reports remain retryable technical
failures. Thresholds, venue policy and execution controls are unchanged.


## Implementation progress — Block 3C

Block 3C closes the verification-time gap without introducing a new market
data contract or a second Yahoo request. `YahooHistorySnapshotProvider`
already performs identity and availability verification using the same frame
that becomes the immutable history snapshot. The replenishment runtime now
injects the exact wave clock into that provider.

Consequently `verification.checked_at`, snapshot acquisition and downstream
history evaluation share one immutable `as_of`. Evidence acquired during the
wave is no longer rejected merely because the system clock advanced by a few
milliseconds after the wave started. True identity mismatch, unavailable
data, malformed history and stale evidence remain fail-closed under their
existing S2.2C and S2.2D contracts.

## Implementation progress — Block 3D

The first clock-aligned LIVE session completed four acquisition waves and
then selected `BIT:BZU` and `BIT:CPR`. Both Yahoo identities were already
represented by current portfolio positions, directly for CPR and through a
leveraged-product proxy for BZU. Their child universe therefore could not
provide the four independent `NEW_LONG`/`NEW_SHORT` outcomes required by a
two-listing acquisition wave.

Block 3D derives the immutable exclusion set from the root Portfolio Watch
Set and removes matching Yahoo identities before frontier selection. The
report records both the complete exclusion identity set and the eligible
listing overlap. These exclusions are not counted as attempted acquisitions.

As a defensive boundary, a directional outcome-count mismatch is now a fatal
orchestration invariant rather than a transient provider failure. It cannot
consume a retry or silently advance the frontier. Retry attempts with the
same wave index are reported chronologically. No Scanner, Watch Universe,
Research, scoring, risk or execution contract is changed.

## Implementation progress — Block 3E

The portfolio-overlap-safe LIVE session
`s4a1-571a62ae08f04abd15a2af20` advanced through five independent candidate
waves. BMPS/BPE demonstrated that one bounded retry can recover a transient
research failure. ENEL/ENI demonstrated the complementary case: after the
retry, ENI SHORT remained `FAILED / PROCESSING_FAILED` because the provider
failed while acquiring Yahoo news. The remaining directional hypotheses were
terminal investment-quality exclusions.

The underlying evidence and research validators remain strict. Invalid
citations, incomplete research and missing targeted repairs still cannot
produce a `TradeOpportunity`. Block 3E changes only the outer control flow:
after the approved retry budget is exhausted, the failed hypothesis and its
listing are quarantined for the immutable replenishment session, no
opportunity is created, and the canonical unattempted frontier continues.

The report records the retry-exhausted outcome count, quarantined listing and
hypothesis identities, reason counts and source research run IDs. Fatal
orchestration invariants still stop immediately, the wave budget remains a
circuit breaker, and all execution side effects remain zero. LIVE validation
and complete project regression remain pending.

## Block 3F — FX clock coherence

The LIVE NASDAQ diagnostic showed successful ECB acquisition with 20 exact
session rates but `TURNOVER_CONVERSION_UNDETERMINED`. ECB rows were stamped
with wall-clock retrieval time while history quality evaluated the immutable
wave `as_of`; valid rows acquired seconds later were therefore discarded as
future evidence.

Block 3F injects the exact wave clock into both Yahoo history and ECB FX
providers. The conversion gate is not relaxed: genuinely missing or invalid
FX evidence remains blocking.

Frontier ordering is delegated to the separately versioned E2E-S4.0A.2
News-Sensitive Acquisition Frontier. Candidate replenishment continues to own
bounded waves, retries, quarantine and the zero-side-effect guarantee.
