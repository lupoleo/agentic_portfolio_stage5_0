# E2E-S2.2D — Real IPO history pilot

## Purpose

This checkpoint validates both history routes with real eligible Scanner listings.

- `NASDAQ:SPCX` started trading on 2026-06-12. At the pilot date it has at
  least 50 completed sessions, so an IPO with otherwise sufficient data must
  graduate to `STANDARD`.
- `NYSE:LYNX` started trading on 2026-08-19. It has fewer than 50 completed
  sessions, so valid short history must remain visible as `RECENT_LISTING`.

The two records are exact listing identities. The SpaceX reference is not
transferred to `VI:SPCX` or `XETRA:SPX`, even though those listings share the
same ISIN.

## Calendar decision

Scanner venue `NASDAQ` is bound explicitly to the `NASDAQ` alias accepted by
`exchange-calendars==4.13.2`. The library resolves that alias to calendar
`XNYS`; the Scanner result continues to preserve listing venue `NASDAQ`.
There is no generic United States fallback.

## Reviewed sources

- SpaceX issuer release: the issuer states that Class A shares began trading
  on Nasdaq on 2026-06-12 under `SPCX`.
- NYSE official contemporaneous IPO video: dated 2026-08-19 and titled
  “Defense Tech Company Lyntris Raises $297.5 Million in IPO”.

The reference file is
`config/scanner/listing_start_references_v1.json`.

## Acceptance

Run the two listings separately. `SPCX` is expected to return exit code `0`
and route `STANDARD`. `LYNX` is expected to return exit code `2` and route
`RECENT_LISTING`; exit code `2` is the deliberate non-standard review route,
not a provider failure. Structural, freshness, price, volume and known
liquidity failures remain blocking for both routes.

## Provider incident observed during acceptance

On 2026-09-23 Yahoo returned an empty 2026-09-22 row
for `IBM`, `MSFT`, `SPCX` and `LYNX`: OHLC and adjusted
close were missing and volume was zero. The current
2026-09-23 row was intraday and therefore not a substitute
for the missing completed session.

The adapter records `EMPTY_PROVIDER_SESSION` while
preserving the row. The history evaluator continues to fail
the price and freshness gates. Neither a confirmed IPO nor
the recent-listing route waives a real provider-data gap.
`SPCX` and `LYNX` should be replayed after Yahoo backfills
the missing session.
