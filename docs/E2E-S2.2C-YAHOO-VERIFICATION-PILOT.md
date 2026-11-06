# E2E-S2.2C — Yahoo Identity and Availability Pilot

## Scope

This incremental delivery follows the offline listing-to-Yahoo resolver.
It adds a Scanner-only verifier and a small sequential live audit. Portfolio
Analysis and its existing Yahoo provider are unchanged.

The offline baseline contains 14,573 eligible listings: 14,444 resolved
candidates and 129 unsupported RO listings. Resolved candidates have not yet
been verified against Yahoo. This delivery does not close S2.2C.

## Verification

Identity and price availability are independent outcomes. VERIFIED requires
matching Yahoo chart metadata for symbol, venue code, currency and instrument
type. Missing fields produce UNVERIFIED; contradictory fields produce
MISMATCH. When both sources provide ISIN, a conflict also produces MISMATCH.
This is provider-metadata consistency, not independent security certification.
Name similarity never verifies identity.

Venue codes are explicit, conservative initial rules requiring live validation.
Unexpected codes are reported rather than accepted as an alternative listing.
LSE GBP/GBp/GBX handling records the price-unit scale when needed; the verifier
does not convert historical prices or perform downstream scoring.

AVAILABLE requires at least one valid daily OHLC bar in the requested window.
Prices must be finite, positive and internally consistent; volume, when
present, must be finite and nonnegative. Timestamps must be timezone-aware,
unique and within the requested interval. This does not establish adequate
history length, liquidity, trading-calendar freshness or execution readiness.

Only RESOLVED + VERIFIED + AVAILABLE with an unexpired result satisfies
the market-data readiness contract. Eligibility remains a separate gate.

## Errors and provenance

- Rate limits and recognized transient network errors remain TEMPORARY_ERROR.
- Missing-price/timezone exceptions remain PROVIDER_ERROR with an indeterminate
  existence diagnostic. They do not prove NOT_FOUND.
- A successful empty response produces NO_DATA.
- Metadata failure can coexist with AVAILABLE prices and UNVERIFIED identity.
- Raw exception text is excluded from diagnostics.
- Results preserve source listing, mapping version, verification version,
  requested interval, timestamps, metadata evidence and diagnostics.

The adapter requests raw daily prices with `raise_errors=True`, without
changing global yfinance configuration. The user's inspected yfinance 1.6.0
signature supports this parameter. Upstream marks it deprecated, so a future
library upgrade requires compatibility verification. See the
[history implementation](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py).

## Pilot operation

The command reads the S2.2B eligibility report and validates mapping collisions
across the entire eligible input before choosing the sample. Default venues
are BIT, XETRA, NASDAQ and NYSE, one eligible listing each. Explicit selection
allows one to three listings per supported venue. No exchange discovery or
universe refresh is performed.

Requests run sequentially with a pause. A report is written atomically after
every completed verification; rate limits stop subsequent requests. An
interruption retains completed results. Unexpected execution failures mark
the report FAILED. Output exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Entire selected sample verified and available |
| 2 | Partial result or rate-limit stop; inspect diagnostics |
| 1 | Execution/input failure |
| 130 | User interruption |

The timeout is passed to history requests. It is not a hard wall-clock budget:
yfinance metadata/internal requests may take additional time. Saved reports
are checkpoints, not a resumable cache. Results carry a 24-hour TTL but this
delivery does not reuse cached verification results.

Persistent cache, resume, broader venue validation and large-universe execution
remain subsequent work. Do not launch the entire universe with this pilot.

## Validation

Offline tests use injected ticker objects and synthetic histories. They cover
identity disagreement, missing metadata, currency units, malformed bars,
missing-data exceptions, rate limits, network errors, sample selection,
pre-selection collision handling and atomic report replacement.

Live acceptance must be run in the user's environment. No successful live
verification is claimed by this document.
