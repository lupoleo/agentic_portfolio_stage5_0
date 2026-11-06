# E2E-S2.1H — Europe V1 Exchange Coverage

## Status

**APPROVED / FROZEN**

Decision date: 2026-09-18.

This milestone freezes the European exchange-acquisition policy for Scanner V1.
It does not yet implement the policy in production configuration.

## Decision

Scanner V1 enables the following European provider exchange codes:

```text
AS
AT
BIT
BR
BUD
CO
HE
LS
LSE
MC
OL
PA
PR
RO
ST
SW
VI
WAR
XETRA
```

The set contains 19 exchanges or exchange adapters.

The order above is deterministic and carries no ranking or execution priority.
All exchange codes not explicitly included remain disabled by default.

## Quantitative evidence

The decision is based on live EODHD exchange discovery and exchange-symbol
audits performed on 2026-09-18, plus the official Borsa Italiana and Deutsche
Börse sources described below.

| Code | Exchange | Valid listings | Common Stock | V1 decision |
| --- | --- | ---: | ---: | --- |
| `BIT` | Borsa Italiana | 40 | 40 | Enable official FTSE MIB fallback |
| `XETRA` | Xetra | 4,192 | 706 | Enable |
| `LSE` | London Exchange | 7,257 | 3,835 | Enable |
| `PA` | Euronext Paris | 1,374 | 629 | Enable |
| `AS` | Euronext Amsterdam | 527 | 110 | Enable |
| `BR` | Euronext Brussels | 115 | 114 | Enable |
| `MC` | Madrid Exchange | 275 | 236 | Enable |
| `SW` | SIX Swiss Exchange | 1,621 | 226 | Enable |
| `ST` | Stockholm Exchange | 998 | 934 | Enable |
| `CO` | Copenhagen Exchange | 607 | 172 | Enable with controlled MIC equivalence |
| `HE` | Helsinki Exchange | 191 | 190 | Enable |
| `OL` | Oslo Stock Exchange | 304 | 297 | Enable |
| `WAR` | Warsaw Stock Exchange | 611 | 581 | Enable |
| `VI` | Vienna Exchange | 88 | 83 | Enable |
| `AT` | Athens Exchange | 143 | 142 | Enable |
| `LS` | Euronext Lisbon | 37 | 37 | Enable |
| `PR` | Prague Stock Exchange | 46 | 45 | Enable |
| `RO` | Bucharest Stock Exchange | 135 | 129 | Enable |
| `BUD` | Budapest Stock Exchange | 48 | 45 | Enable |

Aggregate acquisition size before downstream eligibility:

```text
enabled exchange/adapters: 19
valid listings:            18,609
Common Stock listings:      8,551
```

These figures are point-in-time audit evidence, not permanent invariants.
Provider row counts may change as exchanges add, remove or reclassify listings.

## German exchange decision

The German cross-exchange audit compared:

```text
XETRA, F, STU, MU, DU, HM, HA
```

Across all instrument types it found:

```text
unique ISINs:          14,443
multi-exchange ISINs:   5,456
```

For Common Stock it found:

```text
unique ISINs:          11,780
multi-exchange ISINs:   4,318
```

For ETFs it found:

```text
unique ISINs:           2,552
multi-exchange ISINs:   1,111
```

After XETRA and Frankfurt `F`, the regional German exchanges add only 264 ISINs
across all instrument types, while materially increasing listing volume and
cross-exchange duplication. The canonical listing identity remains
`(exchange, symbol)` and intentionally does not collapse separate exchange
listings by ISIN.

Scanner V1 therefore enables XETRA and defers `F`, `STU`, `MU`, `DU`, `HM` and
`HA` until instrument eligibility, market-data availability, liquidity and
history gates can control the additional universe.

## DAX coverage acceptance gate

The official Deutsche Börse DAX related-values page exposed 40 constituent
instrument pages. Each page was resolved to official name, ISIN, WKN and
ticker, then compared by ISIN with the live EODHD XETRA universe.

Result:

```text
official DAX components:    40
covered by EODHD XETRA:     40
missing:                     0
ticker differences:          0
coverage:                  100%
gate status:               PASS
```

The live XETRA provider returned `PARTIAL`, with 4,192 valid listings and one
explicit row-level diagnostic (`Type must be a string`). That unrelated rejected
row does not reduce DAX coverage and must remain visible rather than being
silently repaired.

The DAX result demonstrates that Frankfurt `F` is not required to cover the
current DAX constituents. DAX membership is an acceptance sample, not a
separate scanner universe and not a replacement for exchange-wide acquisition.

## Italy exception

EODHD does not currently provide the required Italian exchange coverage.

`BIT` therefore uses the official Borsa Italiana FTSE MIB adapter. It is an
explicit `INDEX_FALLBACK` containing 40 listings and must not be represented as
complete coverage of all instruments listed on Borsa Italiana.

The exception remains auditable through provider provenance and composition
metadata.

## Copenhagen controlled equivalence

The EODHD `CO` response contained six otherwise valid rows whose row exchange
value was `XCSE`. The discovered `CO` descriptor declares `XCSE` as its
operating MIC.

Scanner V1 may therefore accept a row only when:

```text
row.Exchange == descriptor.code
OR
row.Exchange == descriptor.operating_mic
```

For Copenhagen this permits the explicitly descriptor-bound equivalence:

```text
CO <-> XCSE
```

This is not a general alias table and does not authorize arbitrary exchange
mismatches. A row matching neither value remains rejected with a diagnostic.

## Explicitly deferred exchanges

| Codes | Reason |
| --- | --- |
| `F`, `STU`, `MU`, `DU`, `HM`, `HA` | German secondary/regional coverage with high cross-exchange overlap and disproportionate listing expansion |
| `IR` | Audit showed 50 funds, only 21 Common Stock listings, 46 missing ISINs and unexpected currency composition |
| `LU` | Five listings only; retained as an integration smoke-test exchange, not production coverage |
| Other discovered European exchanges | Not quantitatively audited and therefore fail closed for V1 |

Deferred does not mean permanently excluded. A later policy version may enable
an exchange after evidence demonstrates useful incremental coverage and the
downstream eligibility stack can control cost and quality.

## Operational semantics

1. The policy enables exchange listings, not equity-index memberships.
2. Enabled exchange membership does not imply scanner eligibility.
3. Canonical identity remains `(exchange, symbol)`.
4. No cross-exchange ISIN deduplication is introduced by this milestone.
5. `PARTIAL` provider results may preserve valid listings but must propagate all
   diagnostics and partial status.
6. A failed provider cannot contribute listings.
7. Exchanges absent from the frozen set remain disabled by default.
8. Exchange discovery remains configuration evidence; it does not implicitly
   enable newly discovered exchanges.
9. Provider cache identity and freshness behavior remain those frozen in
   E2E-S2.1G.

## Non-goals

This milestone does not:

- filter instrument types;
- choose between multiple listings of the same issuer;
- translate provider symbols to Yahoo syntax;
- verify Yahoo market-data availability;
- enforce price-history completeness;
- apply liquidity or minimum-volume thresholds;
- score or rank scanner candidates;
- implement United States aggregate-venue semantics;
- alter Research, Opportunity Scoring or Portfolio Filter behavior.

Those concerns remain assigned to downstream scanner milestones.

## Frozen policy constant

The approved semantic value is:

```python
EUROPE_V1_ENABLED_EXCHANGES = frozenset(
    {
        "AS",
        "AT",
        "BIT",
        "BR",
        "BUD",
        "CO",
        "HE",
        "LS",
        "LSE",
        "MC",
        "OL",
        "PA",
        "PR",
        "RO",
        "ST",
        "SW",
        "VI",
        "WAR",
        "XETRA",
    }
)
```

The constant shown here freezes the decision. Production implementation and
tests are a separate follow-up change.
