# E2E-S2.2D — Exact-date FX liquidity pilot

## Scope

Adds `ECBSessionFXProvider` and extends the history pilot to NYSE/USD.
BIT and XETRA remain EUR-only pilot venues. No other calendar binding is
introduced. The core history policy and existing technical engine are unchanged.

## Source and units

Official source: https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html

Endpoint: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml

ECB quotes foreign currency units per EUR. The adapter inverts each observation
into EUR per major currency unit. This is a reference-rate estimate for the
liquidity gate, not an execution FX price or the equity session closing FX rate.
The last 90 days are sufficient for the current 20-session liquidity window;
older requested dates remain missing, with no fallback.

EUR uses the existing identity conversion. GBP rates are for pounds, not pence;
the existing core applies GBX/GBp scaling separately. This patch does not yet
introduce a London calendar/live pilot.

## Temporal semantics

Use only observations with the exact requested equity session date. No forward
fill, interpolation, synthetic rate, or substitution of another currency.
TARGET holidays can differ from equity-market holidays: a missing reference
rate leaves the conversion undetermined even when the stock traded normally.

`known_at` is the actual retrieval time. A freshly downloaded historical rate
is not claimed to have been known at a past backtest timestamp. The raw response
and SHA-256 are persisted with the acquisition. This allows auditing the precise
version observed; it is not a vintage historical publication database.

The pilot requests the last 20 completed calendar sessions, using the existing
publication grace. If a new session becomes due during acquisition, the core
still evaluates at the final as-of time and fails closed for missing inputs.

## Failure behavior

- Invalid XML, duplicate dates/currencies, nonpositive/nonfinite rates, future
  observation dates and oversized responses produce FX_PAYLOAD_INVALID.
- Network errors produce FX_TRANSPORT_ERROR with exception class only.
- Missing dates produce PARTIAL with explicit missing_sessions.
- Missing FX does not label a stock illiquid. Liquidity remains UNDETERMINED
  and routing is REVIEW_REQUIRED unless another independent gate fails.
- The acquired equity history and identity evidence remain in the report.

The adapter uses the standard library; no new dependency is required. The CLI
makes one ECB request per non-EUR sampled listing. Persistent FX cache and
replay orchestration are not introduced by this patch.

## Validation

Local offline subset: 136 tests passed, including 9 new tests covering inversion,
provenance, missing/old dates, invalid input, transport failure, and the full
USD history-to-liquidity path with injected providers. This is not the full
repository regression and not a live validation.

Prior user-confirmed live baseline: A2A and SAP STANDARD; all gates PASS.
Next live acceptance: NYSE IBM with real ECB USD reference observations.

```powershell
python -m tools.live_scanner_history_quality `
    --input-report .\data\cache\scanner\instrument_audit\scanner_instrument_eligibility_20260921T130324Z.json `
    --venues NYSE `
    --timeout-seconds 20
Write-Output "HISTORY_FX_PILOT_EXIT: $LASTEXITCODE"
```

Exit 0 means all sampled listings are STANDARD; exit 2 means a completed or
partially completed run needs review; exit 1 is a pilot failure. Inspect the
gates rather than forcing a PASS. S2.2D remains open for listing-start evidence
and the recent-listing/IPO integration.
