# E2E-S2.1F — Enabled-Exchange Policy and Provider Composition

## Decision

Scanner V1 uses an explicit enabled-exchange policy and a deterministic
provider-composition boundary.

No exchange is enabled implicitly and there is no default meaning
"all exchanges".

## Components

### Borsa Italiana contract adapter

`BorsaItalianaFTSEMIBExchangeProvider` adapts the frozen official-source
FTSE MIB provider to `ExchangeSymbolProvider`.

The adapter:

- accepts only exchange `BIT`;
- preserves provider status, listings and fetch timestamp;
- converts diagnostics to the common provider contract;
- preserves original diagnostic ISIN values in metadata;
- declares `INDEX_FALLBACK` coverage;
- never claims full Euronext Milan coverage;
- performs no I/O for unsupported requests.

The underlying E2E-S2.1D-IT provider is not modified.

### Enabled-exchange policy

Each policy entry declares:

- exchange code;
- provider identity;
- coverage scope;
- enabled state.

Supported coverage scopes:

- `FULL_EXCHANGE`;
- `INDEX_FALLBACK`.

Policies require at least one enabled exchange, reject duplicate exchange
codes and expose entries in deterministic exchange-code order.

### Provider composition

The composition service:

1. resolves one provider for every enabled exchange;
2. validates provider and result identity;
3. executes each enabled provider once;
4. collects listings and diagnostics;
5. rejects listings returned for the wrong exchange;
6. validates limited-coverage metadata;
7. passes valid raw listings to `build_canonical_universe`;
8. applies explicit listing exclusions;
9. returns an auditable composed-universe result.

## Global status

`SUCCESS` means:

- every enabled provider completed successfully;
- no provider diagnostics were produced;
- canonicalization succeeded;
- the canonical universe is non-empty.

`PARTIAL` means:

- at least one provider returned `PARTIAL` or `FAILED`, or produced
  diagnostics;
- at least one valid canonical listing remains.

`FAILED` means:

- no valid listings were produced;
- canonicalization detected conflicting identity metadata; or
- exclusions removed the complete universe.

A successful `INDEX_FALLBACK` provider does not make acquisition status
partial. Its limited coverage is represented explicitly in policy and
metadata.

## Fail-closed boundaries

The service handles explicitly:

- missing provider registrations;
- provider identity mismatch;
- unexpected provider exceptions;
- invalid provider result types;
- result/request identity mismatch;
- coverage metadata mismatch;
- listing exchange mismatch;
- canonical identity conflicts;
- empty canonical universe.

Unexpected exception messages are not exposed in diagnostics.

## Pilot policy

The S2.1F live-acceptance policy is intentionally small:

- `LU` via EODHD with `FULL_EXCHANGE`;
- `BIT` via Borsa Italiana FTSE MIB with `INDEX_FALLBACK`.

This is not the final production exchange policy.

## Live validation

The initial live composition produced:

- discovery status: `SUCCESS`;
- composition status: `SUCCESS`;
- enabled exchanges: 2;
- successful providers: 2;
- raw listings: 45;
- canonical listings: 45;
- `BIT` listings: 40;
- `LU` listings: 5;
- diagnostics: 0.

## Explicitly deferred

This milestone does not:

- select the final production exchange set;
- cache provider responses;
- define freshness or refresh rules;
- retry failed providers;
- filter instrument types;
- translate symbols to Yahoo syntax;
- verify Yahoo availability;
- apply liquidity or history thresholds;
- invoke Research or Opportunity Scoring.

Cache and freshness belong to E2E-S2.1G.
