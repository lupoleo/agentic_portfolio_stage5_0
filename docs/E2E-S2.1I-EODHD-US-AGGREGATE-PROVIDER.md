# E2E-S2.1I — EODHD United States Aggregate Provider

## Scope

Implement the provider boundary for the EODHD aggregate request scope `US`.

The endpoint returns many provider-native venues in one payload. Request scope
and canonical listing venue are intentionally different concepts.

## Provider identity

```text
provider_id:      eodhd-us-aggregate-symbols
provider_version: 1
source_name:      EODHD US Aggregate Symbols API
request_scope:    US
```

The public `source_url` never contains the API token.

## Row mapping

Each valid row maps to `RawMarketListing` as follows:

```text
Code      -> symbol
Exchange  -> exchange and market
region    -> US
Currency  -> currency
Type      -> instrument_type
Isin      -> optional isin
Name      -> name
Country   -> country
```

The provider preserves venue values such as `NASDAQ`, `NYSE`, `NYSE ARCA`,
`BATS`, `AMEX`, `US`, `NYSE MKT`, `NMFQS` and OTC labels. It does not implement
enabled-venue policy.

## Identity and duplicates

Aggregate-row identity is `(exchange, symbol)`.

- identical duplicate rows are deduplicated and counted;
- the same symbol on different venues is preserved;
- conflicting rows for one `(exchange, symbol)` fail closed;
- successful output is sorted by `(exchange, symbol)`.

No ISIN-based cross-venue deduplication is performed.

## Result states

`SUCCESS` requires at least one valid listing and no rejected rows.

`PARTIAL` requires valid listings plus explicit row diagnostics.

`FAILED` contains no listings and covers unsupported request scope, transport,
JSON, top-level schema, empty-valid-result and conflicting-duplicate failures.

## Metadata

Successful and partial results include:

```text
request_scope
raw_row_count
listing_count
rejected_row_count
duplicate_row_count
response_bytes
venue_count
venue_listing_counts
instrument_type_counts
```

## Security

The token is read from the explicit constructor argument or
`EODHD_API_TOKEN`. It is used only in the private request URL and is not written
to public provenance, diagnostics or metadata.

Network diagnostics report safe exception types rather than provider or host
messages that might contain sensitive request information.

## Architectural boundary

This milestone performs acquisition and faithful row mapping only.

It does not:

- enable or disable United States venues;
- filter instrument types;
- normalize `NYSE MKT` to `AMEX`;
- assign generic `US` rows to an invented exchange;
- integrate the provider into composition;
- integrate the provider into cache/freshness orchestration;
- map symbols to Yahoo syntax;
- apply liquidity or price-history gates.

Those responsibilities remain in later milestones. The frozen S2.1H policy
selects `AMEX`, `BATS`, `NASDAQ`, `NYSE` and `NYSE ARCA` after acquisition.
