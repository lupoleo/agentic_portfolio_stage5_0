# E2E-S2.1 Exchange-Universe Rebase

## Decision

The scanner universe is defined by enabled market exchanges/listings, not by
membership in equity indices.

## Canonical identity

A listing is identified by:

`(exchange, symbol)`

Issuer domicile does not determine eligibility.

## Provider responsibility

The exchange provider returns all available listings for one provider exchange
code as `RawMarketListing` records.

## Canonical-universe responsibility

The canonical core:
- deduplicates by `(exchange, symbol)`;
- preserves listing metadata and source provenance;
- applies explicit listing exclusions;
- restricts to enabled exchanges when requested;
- fails closed on conflicting identity-critical metadata.

## Explicitly deferred

The canonical core does not:
- determine index membership;
- filter instrument types;
- translate symbols to Yahoo syntax;
- check Yahoo availability;
- apply liquidity/history thresholds.

These remain downstream scanner stages.
