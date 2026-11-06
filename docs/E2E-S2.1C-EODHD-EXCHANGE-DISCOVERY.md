# E2E-S2.1C — EODHD Exchange Discovery

## Scope

Discover the exchange codes currently exposed by EODHD before configuring the
Scanner V1 enabled-exchange policy.

Endpoint:

`GET https://eodhd.com/api/exchanges-list/?api_token=...`

The API token is read from `EODHD_API_TOKEN`.

## Security

The token:
- is never included in `source_url`;
- is never written into diagnostics;
- is never printed by the live tool;
- is not persisted by this component.

## Output

Each provider row becomes an `ExchangeDescriptor` containing:
- code
- name
- operating MIC
- country
- currency
- ISO country codes
- local scanner region classification
- virtual-exchange flag

Region classification is intentionally narrow:
- US
- EUROPE
- OTHER
- None for documented EODHD virtual asset-class exchanges.

## Failure policy

Malformed individual rows yield `PARTIAL`.
Transport, JSON, top-level schema, empty-valid-result and duplicate exchange-code
failures yield `FAILED`.

## Non-goals

This milestone does not:
- fetch exchange constituents;
- choose the final enabled-exchange policy;
- apply instrument eligibility;
- call Yahoo/yfinance;
- cache results.

Those remain later S2.1 milestones.
