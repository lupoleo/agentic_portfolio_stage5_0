# E2E-S2.2E — Candidate Set Assembly & Portfolio Watch Set

## Status

- Contract: APPROVED
- Implementation: CLOSED
- Starting baseline: `92b9518`
- Policy: `scanner-v1-candidate-watch-assembly`
- Policy version: `1`

This checkpoint assembles the final Scanner Candidate Set and combines it with
the current Fineco Portfolio Watch Set. It performs no discovery, market-data
download or research call.

## Outputs

S2.2E produces three separate but linked collections.

### Scanner Candidate Set

A listing is included as a new candidate only when:

- instrument eligibility is `ELIGIBLE`;
- Yahoo mapping is `RESOLVED`;
- identity is `VERIFIED`;
- availability is `AVAILABLE` and the verification is current at `as_of`;
- history route is `STANDARD` or `RECENT_LISTING`;
- no history gate is `FAIL`;
- the route-specific liquidity rule passes;
- all upstream identities agree on the exact `ListingKey`.

Every input listing receives a deterministic inclusion or exclusion decision.
Missing upstream results are recorded as `UPSTREAM_RESULT_MISSING`; they are
never interpreted as passing evidence.

### Portfolio Watch Set

Every non-flat Fineco position is included regardless of Scanner new-entry
eligibility. This preserves instruments such as CFDs, certificates, ETFs and
positions on unsupported venues for monitoring, research, reduction, exit or
hedging.

Flat broker rows are explicitly excluded as `FLAT_POSITION`.

The existing mutable `PortfolioPosition` is adapted into a frozen snapshot
record. The adapter retains snapshot identity, broker identity, direction,
quantity, ISIN, currency and deterministic Yahoo resolution when available.

### Research Watch Universe

The union groups records by connected identity evidence while preserving all
source identities and provenance:

- `NEW_CANDIDATE`;
- `CURRENT_POSITION`;
- or both.

Research-subject key precedence is:

1. Yahoo symbol;
2. ISIN;
3. exact listing key;
4. Fineco broker identity.

Exact listing identities and position references are never overwritten by the
grouping key.

## Recent-listing liquidity rule

The approved contract was refined during grounding against the closed S2.2D
implementation.

`STANDARD` requires a `PASS` liquidity gate.

`RECENT_LISTING` may accept liquidity `UNDETERMINED` only when all of the
following are true:

- reason is `INSUFFICIENT_ALIGNED_VOLUME_HISTORY`;
- `recent_listing_verified` is true;
- event is `IPO` or `LISTING_START`;
- no gate is `FAIL`;
- no unexpected gate is `UNDETERMINED`;
- the only permitted undetermined gates are `history_maturity`,
  `technical_inputs` and `liquidity`.

This is not a general relaxation of liquidity. It preserves the explicitly
reviewed IPO/recent-listing route without admitting unknown identity,
freshness, price, calendar, FX or coverage evidence.

## Failure semantics

Expected negative outcomes are represented by reason codes:

- `ELIGIBILITY_NOT_ELIGIBLE`;
- `ELIGIBILITY_REVIEW_REQUIRED`;
- `MAPPING_NOT_RESOLVED`;
- `VERIFICATION_NOT_READY`;
- `UPSTREAM_RESULT_MISSING`;
- `HISTORY_BLOCKED`;
- `HISTORY_REVIEW_REQUIRED`;
- `LIQUIDITY_NOT_PASSING`;
- `RECENT_LISTING_POLICY_NOT_MET`.

Structural contradictions raise errors instead of becoming exclusions. These
include mixed listing keys, mapping/provider mismatches, future history,
duplicate gate names and conflicting duplicate evidence.

## Identity conflicts

- A subject without a Yahoo symbol is retained as `DEGRADED`.
- Several Yahoo listings for one ISIN remain visible as
  `MULTIPLE_MARKET_DATA_LISTINGS`.
- One Yahoo symbol associated with conflicting ISINs becomes
  `REVIEW_REQUIRED` with `IDENTITY_CONFLICT`.

No identity conflict is silently resolved.

## Determinism and replay

The assembly function:

- accepts only supplied evidence;
- performs zero network calls;
- sorts all source decisions and output identities;
- uses aware `as_of` and `assembled_at` timestamps;
- fingerprints the semantic input and output independently of replay time;
- produces the same `run_id` and fingerprint for the same accepted inputs,
  regardless of input ordering.

The audit tool consumes accepted cached reports and a local Fineco snapshot.
It does not claim that listings without S2.2C verification and S2.2D history
evidence are candidates.

Python 3.10 compatibility is explicit: the CLI accepts the seven-digit UTC
round-trip timestamps emitted by PowerShell/.NET, normalizes `Z` to an aware
UTC offset and truncates only precision beyond Python microseconds.

## Acceptance evidence

The focused S2.2E and related gate regression completed with:

- 86 tests passed;
- 35 subtests passed.

The first real cache-only replay correctly admitted no new candidates because
the available upstream evidence was missing, expired or blocked. It still
included all 33 non-flat Fineco positions, demonstrating the fail-closed
candidate path and the independent Portfolio Watch Set path.

Fresh accepted S2.2D `STANDARD` evidence was then acquired for `BIT:A2A` and
`XETRA:SAP`. The final S2.2E replay made zero network calls and produced:

- 31,808 eligibility inputs;
- 2 Scanner candidates;
- 33 portfolio inputs and 33 watched positions;
- 35 Research Watch Universe members;
- 35 members with readiness `READY`;
- provenance counts of 33 `CURRENT_POSITION` and 2 `NEW_CANDIDATE`;
- fingerprint
  `881241c264bde82f3a9633d5766cc21c07a658bc11f43abc2202edd29aff1064`.

The complete regression finished with 1,491 tests and 162 subtests passed.

## Implementation files

- `app/scanner/watch_universe_contracts.py`
- `app/scanner/watch_universe.py`
- `tools/audit_scanner_watch_universe.py`
- `tests/test_scanner_watch_universe_contracts.py`
- `tests/test_scanner_watch_universe.py`
- `tests/test_audit_scanner_watch_universe.py`

## Closure record

S2.2E closed after:

- focused tests passed;
- the cache-only audit completed against real local artifacts;
- the complete regression suite passed;
- `STAGE_4_0_ARCHITECTURE.md` recorded S2.2E as `CLOSED`;
- Scanner-to-Research became the next proposed checkpoint;
- implementation, checkpoint documentation and architecture were included in
  the same versioned change set.
