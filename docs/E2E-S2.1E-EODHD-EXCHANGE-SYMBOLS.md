# E2E-S2.1E — EODHD Exchange Symbol Provider

## Decision

Implement the concrete EODHD boundary behind `ExchangeSymbolProvider`.

One provider instance is bound to one validated `ExchangeDescriptor` and
accepts only the corresponding `ExchangeSymbolRequest`.

## Endpoint

`GET https://eodhd.com/api/exchange-symbol-list/{exchange_code}`

The API token comes from an explicit constructor argument or from
`EODHD_API_TOKEN`.

The auditable `source_url` never contains the API token or query string.

## Discovery dependency

The provider does not infer exchange region or canonical market identity from
individual symbol rows.

Those values come from the `ExchangeDescriptor` validated by E2E-S2.1C.
Virtual or unclassified exchange descriptors are rejected.

## Row mapping

- `Code` → `symbol`
- `Exchange` → `exchange`; it must match the request
- descriptor `name` → `market`
- descriptor `region` → `region`
- `Currency` → `currency`
- `Type` → `instrument_type`
- `Isin` → `isin`, nullable
- `Name` → `name`
- `Country` → `country`
- provider identity → `source`

Instrument types are preserved but not filtered.

## Failure policy

- Malformed individual rows are skipped with `ROW_SKIPPED`.
- Valid listings plus rejected rows produce `PARTIAL`.
- HTTP, network, timeout, invalid JSON and invalid top-level payloads produce
  `FAILED`.
- Descriptor/request mismatch produces `FAILED` before transport.
- Zero valid listings produces `FAILED`.
- Identical duplicate symbols are deduplicated and counted.
- Conflicting duplicate symbols fail closed.
- Listings are ordered deterministically by symbol.
- No incomplete HTTP response is treated as usable data.

## Metadata

The result records:

- exchange code;
- raw row count;
- valid listing count;
- rejected row count;
- identical duplicate count;
- response byte count.

## Explicitly deferred

This milestone does not:

- choose the enabled-exchange policy;
- orchestrate multiple providers;
- filter instrument types;
- translate symbols to Yahoo syntax;
- verify Yahoo availability;
- apply liquidity or history thresholds;
- retry automatically;
- persist or cache responses.

Provider composition is assigned to E2E-S2.1F.

Cache and freshness remain assigned to E2E-S2.1G.

## Validation

Initial live validation:

- exchange: `LU`;
- discovery status: `SUCCESS`;
- provider status: `SUCCESS`;
- raw rows: 5;
- valid listings: 5;
- diagnostics: 0;
- response bytes: 701.
