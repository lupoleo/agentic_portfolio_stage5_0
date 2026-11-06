# E2E-S4.0A.2 — News-Sensitive Acquisition Frontier

Status: `IMPLEMENTED / VALIDATION PENDING`

## Purpose

The acquisition frontier must not be consumed in alphabetical or provider
catalogue order. Those orders are reproducible but contain no investment
information and repeatedly expose the same part of a large universe.

S4.0A.2 adds a deterministic, bounded and replayable priority layer. It uses
recent price-sensitive news only to decide which already-admissible listings
are researched first. It does not weaken eligibility, mapping, history,
liquidity, research, scoring, portfolio-risk or opportunity thresholds.

## Keep-and-refill policy

The policy target is 48 listings and at most 10 screening iterations.

1. Build the acquisition frontier with the existing hard gates and portfolio
   overlap exclusions.
2. Produce a venue-balanced deterministic exploration order derived from the
   immutable session fingerprint and listing key.
3. Screen the first 48 previously unseen listings for recent price-sensitive
   news.
4. Keep every `NEWS_QUALIFIED` listing.
5. If `n` listings qualify, screen exactly `48 - n` new listings in the next
   iteration.
6. Never screen again a listing already classified without qualifying news in
   the same session.
7. Stop when 48 listings qualify, the frontier is exhausted, a provider-wide
   circuit opens, or 10 iterations have completed.

The final priority contains qualified listings first. Any unfilled positions
are taken from listings that have not yet been screened, in the deterministic
seeded order. Previously screened no-event listings are used only if the
frontier cannot otherwise fill the target.

## News qualification

Screening is deterministic and does not invoke an LLM. A news item must:

- fall inside the configured five-day window as of the immutable session
  clock;
- refer directly to the company or listing identity;
- match a versioned price-sensitive event class.

The initial classes cover financial distress, M&A, profit warnings,
regulatory decisions, earnings or guidance, capital actions, material
contracts, legal events, executive changes, analyst actions and material
product or clinical events.

Qualified listings are ordered by:

1. event severity descending;
2. latest publication time descending;
3. direct qualifying item count descending;
4. independent source count descending;
5. deterministic seeded key.

The stored classification states are `NEWS_QUALIFIED`,
`NO_PRICE_SENSITIVE_EVENT`, `NEWS_PROVIDER_UNAVAILABLE`,
`NEWS_RESPONSE_INVALID` and `NOT_SCREENED`.

## Failure and replay semantics

One bounded provider retry is permitted per listing. If every result in an
iteration is unavailable or invalid, the provider circuit opens and the
system falls back to the seeded order. Partial provider success is retained.

`CACHE_ONLY` makes zero news-provider calls. LIVE and CACHE_ONLY both reuse an
existing immutable ranking snapshot for the same replenishment session, so a
resume cannot reorder the frontier or repeat news acquisition.

The snapshot records policy, clock, iterations, assessments, selected
evidence IDs, qualified and fallback identities, network-call count and
circuit state.

## FX-clock correction

The same change set fixes a diagnosed NASDAQ liquidity false negative. Yahoo
history and ECB FX acquisition now use the exact immutable wave clock. ECB
rates fetched during a historical-as-of wave are therefore not discarded as
if they were future evidence merely because the wall clock advanced by a few
seconds.

The FX conversion gate remains mandatory. Missing, malformed or genuinely
unavailable conversion evidence still blocks the candidate explicitly.

## Safety invariants

This checkpoint preserves:

```text
allow_threshold_relaxation = false
allow_execution = false
broker_orders_submitted = 0
portfolio_mutations = 0
automatic_executions = 0
```

News priority is an acquisition scheduler, not an investment decision and not
an opportunity score. All downstream evidence and risk gates remain
authoritative.

## Acceptance required for closure

- keep-and-refill reaches its target without duplicate screening;
- a partial qualifying subset changes the next batch to exactly `48 - n`;
- qualified ordering follows the frozen tuple above;
- provider-wide failure opens the circuit and falls back deterministically;
- CACHE_ONLY makes zero news calls;
- persisted resume makes zero additional news calls and preserves ordering;
- the frontier is not alphabetically ordered;
- Yahoo and ECB providers observe the identical immutable wave clock;
- real NASDAQ candidates no longer fail only because FX `known_at` is later
  than the wave `as_of`;
- focused and complete regression suites pass;
- live evidence preserves all zero-side-effect invariants.
