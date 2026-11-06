# E2E-S2.1H — United States V1 Venue Coverage

## Status

**APPROVED / FROZEN**

Decision date: 2026-09-18.

This decision record freezes the United States venue-acquisition policy for
Scanner V1. It does not implement the dedicated United States aggregate
provider or alter production configuration.

## Decision

Scanner V1 enables the following canonical United States venues:

```text
AMEX
BATS
NASDAQ
NYSE
NYSE ARCA
```

The order above is deterministic and carries no ranking or execution priority.

All other venue labels returned inside the EODHD `US` aggregate remain disabled
by default unless a later policy version explicitly enables them.

## Acquisition scope versus canonical venue

EODHD exposes United States listings through one aggregate request:

```text
GET /api/exchange-symbol-list/US
```

`US` is therefore the provider request scope. It is not automatically the
canonical exchange of every returned row.

The response contains a provider-native `Exchange` value for each listing. The
future aggregate provider must preserve that value as the listing venue. For
example:

```text
request scope: US
row Exchange:  NASDAQ
canonical key: (NASDAQ, <symbol>)
```

The approved policy applies to the row venue, not to the aggregate request
scope.

## Quantitative evidence

The decision is based on a live audit of the EODHD `US` aggregate performed on
2026-09-18.

The complete payload contained:

```text
raw rows:       51,121
response size:  7,615,534 bytes
download time:  2.896 seconds
invalid rows:   0
```

### Enabled venues

| Venue | Rows | Common Stock | ETF | Unique ISIN | Missing ISIN rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| `NASDAQ` | 5,485 | 3,473 | 1,202 | 5,287 | 198 |
| `NYSE` | 3,411 | 2,287 | 226 | 2,777 | 633 |
| `NYSE ARCA` | 2,493 | 19 | 2,470 | 2,480 | 9 |
| `BATS` | 1,543 | 39 | 1,504 | 1,467 | 75 |
| `AMEX` | 296 | 257 | 0 | 267 | 22 |

Aggregate enabled-venue size before instrument eligibility:

```text
raw listings:        13,228
Common Stock:         6,075
ETF:                  5,402
unique ISIN:         12,278
missing ISIN rows:      937
```

Missing ISIN does not by itself invalidate a listing because canonical listing
identity remains `(exchange, symbol)`. ISIN completeness remains visible as a
quality metric and may be used by later eligibility policy.

These counts are point-in-time audit evidence, not permanent invariants.

## Venue overlap evidence

The audit compared enabled candidates by ISIN and symbol.

No shared ISIN or symbol was observed among:

```text
NASDAQ
NYSE
NYSE ARCA
BATS
AMEX
```

The result supports the interpretation that the EODHD aggregate partitions
these instruments by provider-native venue rather than replicating one complete
security universe under every venue label.

The venues also make different material contributions:

- `NASDAQ` and `NYSE` provide the main Common Stock coverage;
- `NYSE ARCA` and `BATS` provide substantial and distinct ETF coverage;
- `AMEX` adds 241 Common Stock ISINs after the preceding venues.

The absence of observed overlap is not promoted to a permanent invariant.
Future payloads may contain migrations, stale records or multiple listings and
must still pass normal canonical-universe conflict checks.

## Generic `US` bucket decision

The aggregate response also contained 498 rows whose row venue was literally
`US`:

```text
rows:                    498
unique ISIN:             246
missing ISIN rows:       252
Common Stock rows:       227
ETF rows:                255
Fund rows:                10
Index rows:                3
Unit rows:                 2
Preferred Stock rows:      1
```

Only one of its ISINs appeared on another regulated candidate venue. The bucket
is therefore not merely a duplicate of NASDAQ or NYSE.

Nevertheless it is excluded from V1 because `US` does not identify a physical
listing venue. Sample inspection also found rights, warrants, units, notes and
fund structures represented under unreliable instrument-type labels, including
several such records classified as `COMMON STOCK`.

The generic bucket may be retained in audit output, but it must not enter the
canonical Scanner V1 universe until a reliable venue and instrument identity
can be established.

It must not be silently distributed among enabled venues or rewritten to an
invented exchange.

## `NYSE MKT` decision

`NYSE MKT` is deferred rather than normalized automatically to `AMEX`.

Audit evidence:

```text
rows:                       35
unique ISIN:                15
missing ISIN rows:          14
Common Stock rows:          19
Preferred Stock rows:       13
ETF rows:                    2
Notes rows:                  1
new ISIN after AMEX:        11
shared ISIN with AMEX:       3
shared symbols with AMEX:    0
```

The small and incomplete dataset, legacy provider label and partial overlap do
not justify an automatic alias rule. A future mapping may be introduced only
after instrument-level evidence establishes the correct current venue.

## Explicitly disabled groups

### Mutual-fund venue

`NMFQS` is disabled:

```text
rows:          25,579
Fund:          20,488
Mutual Fund:    5,083
ETF:                8
```

This is outside the Scanner V1 exchange-listed equity and ETF universe.

### OTC venues

The following labels are disabled:

```text
PINK
OTCQB
OTCGREY
OTCQX
OTCCE
OTCMKTS
OTC
OTCBB
OTCPK
```

Reasons include OTC market structure, large missing-ISIN populations, uncertain
liquidity, inconsistent instrument classification and the absence of the
downstream controls required for safe V1 inclusion.

The largest excluded segment, `PINK`, contained 8,815 rows, including 4,267
without ISIN.

Disabled does not mean permanently unsupported. OTC coverage requires a
separate policy with explicit liquidity, history, quote-quality and instrument
eligibility gates.

## Dedicated provider requirements

The follow-up United States aggregate provider must:

1. perform one acquisition for provider request scope `US`;
2. parse the row `Exchange` value as the provider-native venue;
3. preserve listing identity as `(row exchange, symbol)`;
4. return auditable provider status, metadata and row diagnostics;
5. avoid treating valid disabled-venue rows as malformed input;
6. avoid converting the generic `US` bucket into an enabled venue;
7. avoid automatically aliasing `NYSE MKT` to `AMEX`;
8. keep provider acquisition separate from enabled-venue filtering;
9. use one cache entry for the aggregate `US` acquisition;
10. never persist or expose the EODHD API token.

The provider may acquire all valid venue rows from the aggregate payload.
Enabled-venue policy determines which rows contribute to the canonical Scanner
universe.

## Operational semantics

1. Enabled venue membership does not imply instrument eligibility.
2. The provider boundary preserves source facts; policy selects allowed venues.
3. Instrument types are not filtered by this milestone.
4. Warrants, units, preferred stock, notes, funds and other types remain visible
   until the downstream eligibility stage makes an explicit decision.
5. Canonical identity remains `(exchange, symbol)`.
6. ISIN is important metadata but is not the canonical listing key.
7. No cross-venue ISIN deduplication is introduced.
8. A malformed row produces a diagnostic rather than silent repair.
9. A failed aggregate acquisition contributes no listings.
10. Cache and freshness semantics remain those frozen in E2E-S2.1G.

## Non-goals

This milestone does not:

- implement the United States aggregate provider;
- modify provider composition or cache code;
- filter instrument types;
- map symbols to Yahoo syntax;
- check Yahoo availability;
- require ISIN for every listing;
- apply price, volume, liquidity or history thresholds;
- enable OTC securities;
- select one preferred venue for an issuer;
- alter Research, Opportunity Scoring or Portfolio Filter behavior.

## Frozen policy constant

The approved semantic value is:

```python
US_V1_ENABLED_VENUES = frozenset(
    {
        "AMEX",
        "BATS",
        "NASDAQ",
        "NYSE",
        "NYSE ARCA",
    }
)
```

The constant shown here freezes the decision. Production implementation and
tests belong to the next milestone.
