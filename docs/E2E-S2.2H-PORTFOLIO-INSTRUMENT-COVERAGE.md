# E2E-S2.2H — Portfolio Instrument Coverage & Leveraged-Product Risk Adapters

Status: `CLOSED`

## Purpose

This corrective checkpoint closes the identity and risk-factor gap found after
loading the current 41-position Fineco portfolio on 2026-09-26. It is additive
to S2.2D–S2.2G and does not change scanner eligibility or execution policy.

## Contract

Every broker position remains represented by its original Fineco identity and
accounting market value. A reviewed, versioned registry may attach either:

- a direct provider symbol; or
- an explicitly derived leveraged-return proxy.

Mappings are exact by ISIN or broker symbol. Names are not used as mapping
heuristics. Unreviewed, conflicting or expired references fail closed.

## Reviewed references

| Fineco instrument | Exact key | Source symbol | Method | Direction | Multiplier |
|---|---|---|---|---|---:|
| Kering CFD | `KERCFD.CFD` | `KER.PA` | Direct underlying history | Short | 1x |
| Diasorin fixed leverage | `IT0005686685` | `DIA.MI` | Leveraged proxy | Long | 5x |
| Fincantieri fixed leverage | `IT0005686883` | `FCT.MI` | Leveraged proxy | Short | 5x |
| Moncler fixed leverage | `IT0005687683` | `MONC.MI` | Leveraged proxy | Short | 5x |
| Buzzi fixed leverage | `IT0005687667` | `BZU.MI` | Leveraged proxy | Short | 5x |

The operator supplied the primary Fineco evidence on 2026-09-26. Screenshots
are not committed because they contain private account information.

## Exposure and return semantics

Kering's Fineco market value is already full notional exposure. The displayed
20% margin / X5 is informational and is never applied a second time.

For each certificate:

- accounting exposure equals the absolute Fineco market value;
- risk-factor returns equal five times underlying returns;
- economic direction is applied separately by the portfolio risk engine;
- the synthetic factor ID is unique per ISIN;
- product price, product liquidity and provider price gap are not fabricated.

This prevents double leverage, accidental aggregation with the direct
underlying, and presentation of a derived series as a traded product price.

## Closure evidence

E2E-S2.2H closed on 2026-09-27 with the following evidence:

- the focused contract and audit suite passed 14 tests;
- the offline portfolio audit represented 41 of 41 Fineco positions;
- 37 positions use direct history and four use reviewed leveraged proxies;
- all five reviewed references were matched with zero audit network calls;
- the five-symbol live provider pilot passed for `KER.PA`, `DIA.MI`,
  `FCT.MI`, `MONC.MI` and `BZU.MI`;
- every leveraged proxy uses a unique ISIN-based factor ID, applies the 5x
  multiplier only to underlying returns and keeps economic direction separate;
- the refreshed workbook contains 41 position rows, 37 direct rows and four
  proxy rows with explicit source, method, leverage and reference provenance;
- Fineco accounting gross exposure remained EUR 304,652.72 and total portfolio
  weight remained 100%;
- Stage 3 persisted PortfolioSnapshot
  `SNAP-20260927-075500-6b4271`;
- the complete project regression passed 1,532 tests and 162 subtests;
- no broker order, portfolio mutation or automatic execution was authorized.

Extended analytical coverage is 94.83%. The uncovered portion is caused by the
explicit minimum-history rule for `2BTC.DE`, `NBIS` and `SPCX`, not by missing
S2.2H mappings. The 10-day tail-risk calculation additionally excludes
`USSMC.MI` and reports 91.35% coverage. These statistical limitations remain
visible and fail neither identity coverage nor instrument representation.

## Non-goals

The checkpoint does not authorize execution, infer missing products, model
certificate issuer credit/spread/knock-out mechanics, or treat proxy histories
as actual certificate prices or volumes.
