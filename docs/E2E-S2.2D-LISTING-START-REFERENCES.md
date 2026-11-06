# E2E-S2.2D - Reviewed listing-start evidence

## Boundary

ListingStartReferenceRegistry consumes an explicitly reviewed primary-source
manifest. It validates schema, chronology, exact listing key and available ISIN
consistency. It does not independently authenticate the publisher, verify the
truth of the excerpt or certify that a reviewer performed the review.

The reviewer must establish the actual first trading session for the specific
security on the specific venue. An expected IPO date, offering price date,
settlement date, ticker-change date or first Yahoo history row is insufficient.

Prefer an exchange notice, issuer announcement confirming trading began, or
regulator document. Preserve a short supporting excerpt and the source URL.

IPO requires explicit confirmation of the IPO event. A transfer, secondary
listing, direct listing, spin-off or other start of trading is not automatically
an IPO. LISTING_START preserves that distinction.

## Manifest

Suggested local path:
data/reference/scanner/listing_start_references.json

The following template is intentionally not admissible until completed and
independently reviewed. Never change review status just to pass validation.

```json
{
  "schema": "scanner-listing-start-references-v1",
  "references": [
    {
      "exchange": "NYSE",
      "symbol": "REPLACE_WITH_ACTUAL_SYMBOL",
      "issuer_name": "REPLACE_WITH_ACTUAL_ISSUER",
      "isin": null,
      "first_session": "YYYY-MM-DD",
      "event_kind": "IPO",
      "ipo_confirmed": false,
      "date_status": "EXPECTED",
      "source_kind": "EXCHANGE",
      "source_url": "https://REPLACE_WITH_PRIMARY_SOURCE",
      "source_title": "REPLACE_WITH_SOURCE_TITLE",
      "source_excerpt": "REPLACE_WITH_SHORT_SUPPORTING_EXCERPT",
      "observed_at": "REPLACE_WITH_AWARE_TIMESTAMP",
      "reviewed_at": "REPLACE_WITH_AWARE_TIMESTAMP",
      "reviewed_by": "REPLACE_WITH_REVIEWER",
      "review_status": "PENDING"
    }
  ]
}
```

An operational entry requires:

- review_status: APPROVED
- date_status: CONFIRMED_TRADING_START
- source_kind: EXCHANGE, ISSUER or REGULATOR
- timezone-aware observation and review timestamps
- reviewed_at >= observed_at
- first_session <= observation date
- ipo_confirmed=true for IPO
- ipo_confirmed=false for LISTING_START

Duplicate JSON fields and duplicate listing keys are rejected, even if
identical. There is no last-record-wins behavior.

A reused symbol requires review against the current security. Missing ISIN
does not establish identity continuity.

## Temporal and audit semantics

Known-at is the review timestamp. Evidence reviewed today cannot justify a
decision as of yesterday.

The canonical record SHA-256 binds the source, excerpt, listing identity,
event, timestamps and reviewer. Full reference details and resolution status
are persisted in the pilot report.

Missing references do not infer a listing date from price history.
Future-known references and ISIN conflicts prevent evaluation of that sample
instead of being silently discarded.

The core checks calendar consistency and bars preceding the stated start.
Recent-listing status never waives invalid prices or missing expected sessions.

## Pilot integration

The existing CLI accepts:

- --listing-references PATH
- --listing EXCHANGE:SYMBOL, repeated at most three times

Explicit selections must belong to the selected venues, remain ELIGIBLE in
the complete input report and resolve without global mapping collisions.

A manually selected IPO cannot bypass an older eligibility report that does
not contain it. Refresh upstream discovery and eligibility separately when
necessary.

Supported pilot venues remain BIT, XETRA and NYSE.
No NASDAQ calendar fallback is introduced.
The pilot prints the listing event and available/required indicator sessions.

RECENT_LISTING is a dedicated research route, not automatic trade approval or
standard technical readiness.

Exit semantics:

- 0: all samples STANDARD
- 2: RECENT_LISTING, REVIEW_REQUIRED, BLOCKED or other partial outcomes
- 1: tool failure
- 130: interrupted

Read the route and individual gates rather than exit 2 alone.

Insufficient short-history liquidity remains UNDETERMINED, never assumed PASS.
LONG/SHORT signals, borrow availability, sizing and execution remain downstream.

## Verification status

The previous local offline subset passed 152 tests, including 16 new tests
covering reviewed references and the recent-listing pipeline.

Cases include exact key matching, ISIN conflict, source/review chronology,
duplicate records, record fingerprinting, 5-session IPO preservation,
15/21-session indicator availability, 50-session transition, invalid data,
insufficient liquidity and selection respecting eligibility.

All IPO fixtures are synthetic. Real IPO live acceptance remains pending.

Previously user-validated live samples:
A2A and SAP in EUR; IBM in USD, with 20 same-date ECB rates and no missing dates.

S2.2D remains open pending final acceptance, full repository regression and
a real reviewed-reference pilot.
