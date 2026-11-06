# Stage 2.3 — Historical VaR and CVaR / Expected Shortfall

Stage 2.3 adds non-parametric historical tail-risk measures to the LONG/SHORT-aware portfolio engine.

## Measures

For each requested confidence level (default 95% and 99%):

- **Historical VaR**: positive loss threshold corresponding to the lower historical return quantile.
- **CVaR / Expected Shortfall**: positive average loss in observations at or below the VaR return threshold.
- Both percentage and EUR values are reported.

## LONG / SHORT convention

Positions are aggregated by Yahoo symbol. The historical portfolio return is:

`r_portfolio,t = sum(signed_weight_i * r_i,t)`

where:

`signed_weight_i = signed_exposure_i / total_gross_exposure`

LONG exposure is positive and SHORT exposure is negative. Percent risk figures are therefore expressed relative to gross exposure, consistently with Stage 2.2.

## Horizons

The standard report calculates:

- 1-day Historical VaR/CVaR
- 10-day Historical VaR/CVaR

For horizons above one day the engine compounds actual rolling portfolio returns. It does **not** use square-root-of-time scaling.

## Coverage

Assets without sufficient price history are excluded from the historical simulation and reported explicitly. `Risk coverage` reports the percentage of total gross exposure represented by eligible assets.

## Interpretation

VaR is not a maximum possible loss. A 95% 1-day VaR estimates a historical loss threshold exceeded in roughly 5% of observations under the current portfolio weights.

CVaR/Expected Shortfall measures the average loss in that tail and is therefore more informative about severity beyond VaR.

## Limitations

- Historical simulation assumes the observed return history is informative about future tail behavior.
- Current position weights are applied retrospectively; this is not a reconstruction of the portfolio's actual historical holdings.
- With approximately one year of daily data, 99% estimates are based on very few tail observations and should be treated as unstable.
- Explicit FX risk for non-EUR securities is not yet modeled.
- Liquidity, gaps beyond observed history, leverage financing costs, options nonlinearities, and stress scenarios are not yet modeled.
