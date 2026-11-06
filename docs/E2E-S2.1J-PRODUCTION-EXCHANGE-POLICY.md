# E2E-S2.1J — Production Exchange Policy Integration

## Decision

Scanner V1 distinguishes an enabled listing venue from the provider acquisition
scope used to obtain it.

An `EnabledExchange` therefore has both:

- `exchange_code`: canonical listing venue admitted to the scanner universe;
- `acquisition_code`: request scope passed once to the registered provider.

When omitted, `acquisition_code` defaults to `exchange_code`. Existing
single-exchange providers and the S2.1F pilot policy remain compatible.

## United States aggregate acquisition

The EODHD United States endpoint is requested once with acquisition code `US`.
It returns listings for multiple concrete venues. Scanner V1 enables only:

- `AMEX`
- `BATS`
- `NASDAQ`
- `NYSE`
- `NYSE ARCA`

All five entries share:

- provider `eodhd-us-aggregate-symbols`;
- acquisition code `US`;
- coverage scope `AGGREGATE_VENUES`.

The complete provider result can be cached under request scope `US`. The
canonical-universe stage subsequently filters listings to the enabled venues.
Listings from `NMFQS`, `PINK`, `NYSE MKT`, the generic `US` bucket and other
disabled venues do not enter the production universe.

This avoids repeated downloads and preserves the source representation for
auditing and future policy changes.

## European production policy

The full-exchange EODHD acquisitions approved in S2.1H are:

`AS`, `AT`, `BR`, `BUD`, `CO`, `HE`, `LS`, `LSE`, `MC`, `OL`, `PA`, `PR`,
`RO`, `ST`, `SW`, `VI`, `WAR`, `XETRA`.

Each uses the same code for acquisition and canonical exchange.

Italian coverage remains the official Borsa Italiana FTSE MIB index fallback:

- exchange and acquisition code `BIT`;
- provider `borsa-italiana-ftse-mib`;
- coverage scope `INDEX_FALLBACK`.

The production policy therefore contains 24 enabled listing exchanges backed
by 20 provider acquisitions.

## Composer invariants

The provider registry is keyed by acquisition code, not output exchange.

The composer:

1. groups enabled entries by acquisition code;
2. executes each acquisition exactly once;
3. validates provider and result identity against that acquisition;
4. preserves the provider result for audit;
5. aggregates all returned raw listings;
6. filters the canonical universe by enabled listing exchanges.

For `FULL_EXCHANGE` and `INDEX_FALLBACK`, listings outside the declared output
exchange still fail closed. `AGGREGATE_VENUES` explicitly permits additional
provider venues because filtering occurs downstream.

Entries sharing one acquisition code must declare the same provider identity
and coverage scope. Conflicting acquisition contracts are rejected when the
policy is constructed.

## Cache behavior

No cache format change is required. `CachedExchangeSymbolProvider` receives
the acquisition request and therefore naturally stores the aggregate United
States result under `US`. The cache remains outside policy filtering.

## Explicitly deferred

This milestone does not:

- change instrument-type eligibility;
- translate provider symbols to Yahoo syntax;
- verify market-data availability;
- apply liquidity or history thresholds;
- select scanner candidates;
- invoke Research or Opportunity Scoring.

Those remain downstream scanner milestones.
