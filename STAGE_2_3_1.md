# Stage 2.3.1 — Risk-window stabilization

Stage 2.3.1 stabilizes the Stage 2.2/2.3 risk estimates without changing the existing LONG/SHORT conventions.

## Separate analytical windows

The application now downloads a wider Yahoo history (`5y`) once.

Each analytical layer then uses only the data it needs:

- Technical analysis: maximum 252 trading sessions (~1 year).
- Extended covariance and historical tail-risk analysis: maximum 756 return observations (~3 years).

This keeps SMA/RSI/momentum interpretation on the intended one-year technical horizon while giving VaR/CVaR and covariance estimates a larger statistical sample.

## Why the download period is 5y

Yahoo/yfinance does not consistently expose `3y` as a standard period. Downloading `5y` and explicitly limiting the risk sample to 756 observations makes the application independent of that provider-specific limitation.

## Minimum extended-risk history

The command-line portfolio report requires 504 common daily observations (approximately two trading years) for the extended risk sample.

An instrument with less history is excluded from the extended covariance / historical simulation rather than shortening the entire portfolio sample. Its exclusion is visible in:

- `Excluded (short history)`
- `Risk coverage (gross exposure)`

This is deliberate: an extended estimate with reduced coverage is preferable to silently describing a 200-session calculation as a three-year estimate.

## Tail robustness warnings

Historical VaR/CVaR output now warns when the empirical tail contains few observations:

- fewer than 5: VERY LOW robustness
- 5–9: LOW robustness
- 10–19: MODERATE robustness
- 20 or more: no sample-size warning

A separate warning is printed when extended risk coverage falls below 95% of gross exposure.

These warnings describe statistical sample robustness; they do not convert VaR into a worst-case loss estimate.

## LONG / SHORT

No LONG/SHORT convention changes were made.

Signed weights remain:

`LONG = positive`

`SHORT = negative`

and are normalized by total gross exposure. SHORT positions therefore continue to act as hedges only when their historical covariance actually reduces portfolio risk.

## Output cleanup

VaR and CVaR percentages and EUR amounts are now visually separated in the console table.

The report also states the technical and extended-risk windows explicitly so 20-day technical volatility is not confused with the longer covariance volatility estimate.
