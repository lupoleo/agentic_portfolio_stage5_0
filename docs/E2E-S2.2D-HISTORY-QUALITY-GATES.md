# E2E-S2.2D — History Quality & Liquidity Gates

## Status and scope

First implementation block: immutable input contracts, pure deterministic
evaluator and offline tests. S2.2D is not closed. Network acquisition, calendar
and FX adapters, history persistence, live audit and downstream orchestration
remain subsequent blocks. Existing Portfolio Analysis code is unchanged.

Baseline main before this block: 91a752a; user-confirmed full regression
1382 passed and 91 subtests passed. Do not describe the new tests as a full
repository regression until run in the user's repository.

## Input contract

`HistorySnapshot` preserves the exact listing key, Yahoo symbol, mapping version,
retrieval timestamp, inclusive session-date range, provider price unit, source,
adjustment basis and immutable daily observations. Each observation has nominal
OHLC, a compatible share-volume observation and an optional adjusted close.
The producer must establish compatibility of historical price/volume bases,
including corporate actions. No dividend-adjusted close is used for turnover.
The core does not infer or repair corporate-action data.

Use `MarketDataVerification` from S2.2C. Admission requires a fresh VERIFIED /
AVAILABLE result, with matching listing, Yahoo symbol and mapping version.
Currency units must agree with retained Yahoo metadata. A successful response
on another listing never supplies this verification.

Session labels are local exchange trading dates; closes are timezone-aware
instants. `SessionCalendar` explicitly declares complete coverage and source /
version. Its producer owns holidays, exceptional closures and early closes.
No weekday-only fallback is used. Coverage must include the requested snapshot
range and the as-of UTC date; this is intentionally conservative.

Optional `ListingStartEvidence` must be verified upstream for this exact listing,
with first session, source, known_at and event kind. The earliest returned price
row is not listing-start evidence. IPO is an explicit sourced event classification,
not a synonym for short history or a recent secondary listing.

`SessionFXRate` supplies EUR per one major currency unit for a specific session,
with source and known_at. Missing or not-yet-known rates cannot be guessed or
forward-filled. EUR uses unity; GBp/GBX prices are divided by 100 before GBP FX.
In this first block daily rates must match the session exactly.

All evidence is supplied by callers; evaluation performs zero network calls.
Only successfully acquired snapshots should reach this evaluator: transport
errors remain upstream availability diagnostics, not synthetic empty histories.

## Policy v1

| Parameter | Default |
| --- | --- |
| Minimum standard history | 50 valid completed price sessions |
| Recent coverage window | 60 expected sessions |
| Minimum recent coverage | 95% |
| Liquidity window | 20 completed sessions |
| Minimum positive-volume sessions | 19 of 20 |
| Minimum median estimated daily turnover | EUR 1,000,000 |
| Publication grace after official close | 1 hour |

The one-hour grace is an explicit configurable implementation default, not a
provider service guarantee. Sourced listing-start evidence shortens coverage
to the actual listing lifetime when it is shorter than 60 sessions. An input
window truncated to 20 or 50 sessions without that evidence cannot claim full
coverage. Acquisition should request about one year; the evaluator reports the
actual interval and count rather than assuming that request was fulfilled.

Defaults are engineering policy choices, not market facts or calibrated trading
recommendations. Full policy values and fingerprints accompany every decision;
production policy changes require a version update.

## Gates and indicator capability

Every gate records PASS, FAIL or UNDETERMINED with a reason code. Results preserve
independent checks for verification, snapshot time, listing reference, calendar,
structure, prices, volume integrity, coverage, freshness, technical inputs,
history maturity, price units and liquidity.

Only sessions closed plus publication grace at `as_of` are evaluated. The newest
such session must be present. Pending bars are counted and excluded. Duplicate,
unexpected, out-of-window or pre-listing bars fail structural quality. Invalid
nominal OHLC or negative/infinite/non-numeric volume is a hard quality failure;
absent volume/NaN remains missing, never fabricated as zero. Missing sessions
remain visible in coverage and in indicator alignment; there is no interpolation.

Indicator capability means input sufficiency, not a calculated trading signal:

| Capability | Consecutive expected sessions required |
| --- | --- |
| SMA20 | 20 adjusted closes |
| SMA50 / trend | 50 adjusted closes |
| RSI14 | 15 adjusted closes |
| 20-return volatility | 21 adjusted closes |
| RVOL20 | 21 volumes, with positive average over the previous 20 |

Trailing counts stop at a missing/invalid expected observation. Independent
dropna operations must not silently join observations across missing sessions.
Recent-history capability does not invoke the legacy full technical analyzer,
which requires at least 50 rows and 50 valid closes.

Liquidity uses median(nominal close x compatible volume x unit scale x session
FX), over exactly the most recent 20 completed sessions. It is a daily turnover
proxy, not official traded value, bid/ask depth or broker execution capacity.
No complete window => UNDETERMINED; proven low turnover => FAIL. Missing FX or
unit metadata => UNDETERMINED. Positive-volume failures can be determined without FX.

## Routing and IPO preservation

1. Any definite mandatory-gate FAIL => BLOCKED for the standard candidate flow.
2. Other missing mandatory evidence => REVIEW_REQUIRED.
3. A verified recent listing with fewer than 50 expected completed sessions and
   no quality failures => RECENT_LISTING. Short liquidity/indicator windows may
   remain UNDETERMINED; this route does not grant standard admission.
4. All gates PASS and all standard indicator inputs available => STANDARD.
5. Otherwise => REVIEW_REQUIRED.

Insufficient maturity alone never produces BLOCKED. A five-session IPO remains
RECENT_LISTING if its lifetime coverage and other known quality checks pass.
A recently listed instrument with 50 valid aligned sessions may become STANDARD;
its IPO/listing event evidence remains attached. A verified mature listing with
only a short returned fragment is not relabeled an IPO.

When a recent listing has missing mandatory identity/calendar/unit evidence,
its route is REVIEW_REQUIRED and the result still retains listing-event evidence
and the recent_listing_verified metric where established. Even blocked results
are retained as auditable decisions; no source listing is deleted.

Upstream instrument eligibility remains required for new Scanner candidates.
These gates do not remove existing portfolio positions, score opportunities,
select LONG/SHORT direction or establish borrow/CFD availability.

## Audit API

Call `evaluate_history_quality(snapshot=..., verification=..., as_of=...,
calendar=..., listing_start=..., fx_rates=..., policy=...)`.
The result contains gates, indicator capabilities, measurements, the full policy,
source references and SHA-256 fingerprints of snapshot and evaluation evidence.
`history_quality_result_to_dict(result)` produces a JSON-safe audit object;
publication_grace is serialized in seconds, dates/times in ISO format.
The caller must retain the input snapshot and reference evidence for replay;
fingerprints are not substitutes for stored inputs. Point-in-time research also
requires historically appropriate adjustment/calendar evidence from producers.

The core raises ValueError for mixed identities, calendars for another venue,
duplicate FX keys and malformed policy/reference contracts. Data deficiencies
produce structured gates. Inputs and existing S2.2C results are never mutated.

## Validation and remaining work

31 new offline unittest tests passed; 103 tests passed across the available local
S2.2C/S2.2D subset. Synthetic calendars and FX rates are explicitly test fixtures,
not live reference data. Tests cover IPO preservation, indicator boundaries,
calendar gaps and holidays, publication grace, missing bars/volumes, invalid OHLC,
FX availability, pence scaling, expiry, provenance and repeatability.

Next: bind real historical frames to these contracts; provide explicit calendar,
listing-start and FX sources; retain history snapshots; then run a bounded live
audit before integrating with candidate generation. Do not close S2.2D or claim
production liquidity coverage based only on these offline tests.
