# E2E-S2.2A — Instrument-Type Taxonomy & Eligibility Audit

## Status

Instrument taxonomy is defined. Eligibility remains deliberately undecided and
is assigned to S2.2B.

## Audited production universe

The audit used the cached S2.1J production universe:

- 31,808 canonical listings;
- 24 enabled listing venues;
- 20 provider acquisitions;
- identity retained as `(exchange, symbol)`;
- no eligibility filter applied.

## Observed provider taxonomy

| Canonical category | Listings |
| --- | ---: |
| Common stock | 14,630 |
| ETF | 14,628 |
| Fund | 1,003 |
| Preferred stock | 734 |
| Warrant | 369 |
| Unit | 191 |
| Note | 138 |
| ETC | 73 |
| Index | 34 |
| Mutual fund | 5 |
| Bond | 3 |

The official BIT adapter emits `COMMON_STOCK`, while EODHD emits
`COMMON STOCK`. They are explicit synonyms of canonical `COMMON_STOCK`.

## Canonical taxonomy

The deterministic canonical values are:

- `COMMON_STOCK`
- `PREFERRED_STOCK`
- `ETF`
- `ETC`
- `FUND`
- `MUTUAL_FUND`
- `BOND`
- `NOTE`
- `WARRANT`
- `UNIT`
- `INDEX`
- `UNKNOWN`

Only explicit raw-value mappings are permitted. Unknown and missing values map
to `UNKNOWN` and remain unrecognized.

Every resolution preserves:

- the original provider value;
- its normalized raw representation;
- the canonical type;
- whether the raw value was recognized.

## Provider-label reliability

The provider type is useful but not authoritative. A word-boundary review of
the 14,630 listings declared as common stock found:

- 63 security-form review candidates;
- 345 collective-vehicle review candidates.

Most strong mismatches are NASDAQ SPAC rights. Other examples include a SPAC
unit, a warrant, subscription rights, an ETP, a structured note and
exchange-traded debt.

The review also demonstrated why names alone cannot perform deterministic
reclassification:

- `NOTE AB` is a company name;
- `Law Debenture Corp` is a listed company;
- `Rights and Issues Investment Trust` contains “Rights” in its issuer name;
- listed partnerships legitimately use “Unit” in their security names;
- investment trusts and closed-end funds may issue exchange-listed ordinary
  shares while still requiring a separate product-policy decision.

Name and symbol heuristics may therefore produce review flags, but they must
not silently rewrite canonical instrument type.

## ISIN findings

ISIN is absent from 2,280 declared common-stock listings and 1,546 ETFs. It
cannot be a mandatory V1 taxonomy or eligibility prerequisite.

There are 3,250 ISIN groups represented on more than one enabled venue. This is
expected cross-listing behavior and does not change canonical listing identity.

## Hypothetical scenarios

These figures are audit scenarios, not approved policy:

| Scenario | Listings | Universe share | Venues |
| --- | ---: | ---: | ---: |
| Common stock only | 14,630 | 45.995% | 24 |
| Common stock + ETF | 29,258 | 91.983% | 24 |
| Common stock + ETF + preferred | 29,992 | 94.291% | 24 |

## Explicitly deferred to S2.2B

S2.2A does not decide:

- which canonical categories are eligible;
- whether collective investment vehicles are eligible;
- whether suspected provider misclassifications fail closed;
- venue-specific symbol/name exclusion rules;
- market-data availability, liquidity or history thresholds.

Those decisions belong to the explicit Instrument Eligibility Policy.
