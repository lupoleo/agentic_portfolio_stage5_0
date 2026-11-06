# Stage 2.2 — Covariance-Aware Portfolio Risk

This stage adds covariance-aware market-risk analysis while preserving Stage 2.1 exposure and concentration metrics.

## Core conventions

- LONG positions use positive signed exposure.
- SHORT positions use negative signed exposure.
- Broker market-value sign is ignored for direction; direction comes from position quantity/direction.
- Gross exposure is the sum of absolute economic exposures.
- Covariance weights are signed exposure divided by portfolio gross exposure.
- Multiple Fineco positions mapped to the same Yahoo symbol are aggregated before covariance calculations.

## Metrics

- Annualized covariance matrix from daily adjusted-price returns.
- Correlation matrix.
- Annualized portfolio volatility: `sqrt(w.T @ Sigma @ w)`.
- Marginal risk: `(Sigma @ w) / portfolio_volatility`.
- Component risk: `w * marginal_risk`.
- Risk contribution %: `component_risk / portfolio_volatility`.
- Weighted standalone volatility.
- Diversification ratio.
- Gross-exposure risk coverage and excluded symbols with insufficient history.

Component risk may be negative. A negative contribution indicates that, over the estimation window, the signed position reduces modeled portfolio volatility.

## Estimation defaults

- 252 trading sessions annualization.
- Minimum 60 common daily return observations.
- Returns use Yahoo adjusted `Close` prices.

## Known limitation

For instruments quoted in currencies other than EUR, this stage models local-market percentage returns but does not yet add explicit EUR FX return risk. FX-aware covariance can be added in a later stage.
