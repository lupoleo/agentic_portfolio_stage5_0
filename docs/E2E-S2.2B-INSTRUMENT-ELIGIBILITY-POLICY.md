# E2E-S2.2B — Instrument Eligibility Policy

## Decision

Scanner V1 automatically admits only canonical `COMMON_STOCK` listings as new
opportunity candidates.

Eligibility applies to new Scanner candidates. It does not remove existing
portfolio positions from the future Portfolio Watch Set.

## Outcomes

Every listing receives exactly one operational outcome:

- `ELIGIBLE`
- `INELIGIBLE`
- `REVIEW_REQUIRED`

Every decision records listing identity, raw provider type, canonical type,
outcome, reason code, review flags and policy identity/version.

## V1 type policy

| Canonical type | Outcome | Reason |
| --- | --- | --- |
| `COMMON_STOCK` | Eligible unless flagged | `TYPE_ELIGIBLE` |
| `ETF` | Ineligible/deferred | `TYPE_DEFERRED` |
| `ETC` | Ineligible/deferred | `TYPE_DEFERRED` |
| `PREFERRED_STOCK` | Ineligible/deferred | `TYPE_DEFERRED` |
| `FUND` | Ineligible | `TYPE_INELIGIBLE` |
| `MUTUAL_FUND` | Ineligible | `TYPE_INELIGIBLE` |
| `BOND` | Ineligible | `TYPE_INELIGIBLE` |
| `NOTE` | Ineligible | `TYPE_INELIGIBLE` |
| `WARRANT` | Ineligible | `TYPE_INELIGIBLE` |
| `UNIT` | Ineligible | `TYPE_INELIGIBLE` |
| `INDEX` | Ineligible | `TYPE_INELIGIBLE` |
| `UNKNOWN` | Ineligible/fail closed | `TYPE_UNKNOWN` |

Deferred types are potentially useful future product categories but require
their own market-data, scoring, execution and risk treatment.

## Classification review

An eligible provider type can be contradicted by strong security-form evidence
in the official provider name. Conservative word-boundary rules recognize:

- rights or subscription rights;
- warrants;
- units;
- ETF/ETP markers;
- explicit debt-form phrases.

Such a listing becomes `REVIEW_REQUIRED` and is not automatically admitted.
The flag does not rewrite canonical taxonomy.

Rules deliberately avoid broad substring matching. Known false positives such
as `NOTE AB`, `Law Debenture Corp`, `Rights and Issues Investment Trust`,
`United`, `Netflix` and `Funding Circle` remain unflagged.

Collective vehicles represented by provider-listed common shares are not
silently reclassified by name. Their product-policy treatment can be refined in
a later milestone when stronger reference data is available.

## Non-requirements

Eligibility does not require ISIN. S2.2A found 2,280 common-stock listings
without one.

This milestone does not apply:

- Yahoo symbol translation or availability;
- liquidity thresholds;
- history thresholds;
- technical signals;
- portfolio membership filtering;
- Research or Opportunity Scoring.

Those gates remain downstream and must preserve the auditable eligibility
decision.
## Validation — 2026-09-21

- Focused regression: 63 passed.
- Full regression: 1,310 passed.
- Production-universe eligibility audit: SUCCESS; exit code 0.
- Input universe: 31,808 listings across 24 venues.
- Eligibility decisions: 31,808.
- ELIGIBLE: 14,573.
- INELIGIBLE: 17,178, comprising 15,435 deferred and 1,743 excluded.
- REVIEW_REQUIRED: 57.
- All 40 BIT fallback listings remain eligible.

Review counts and samples include only REVIEW_REQUIRED decisions.
The 58 review flags refer to 57 listings: GRAF-U carries both UNIT and WARRANT.

The underlying acquisition retains the known PARTIAL results for CO and
XETRA; audit success does not imply complete or error-free source coverage.
Cache-only applies to exchange-symbol acquisition; discovery still uses
the network.

Eligibility is a type-policy gate, not evidence of tradability or a LONG/SHORT
signal. Name-based review rules are conservative heuristics and do not
guarantee detection of every provider classification error.
