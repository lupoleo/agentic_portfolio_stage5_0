# Stage 2.4 — Benchmark Risk

Stage 2.4 adds benchmark-relative risk analysis while preserving all Stage 2.3.1 calculations and LONG/SHORT conventions.

## Default benchmark

The command-line report uses `SPY` by default:

`BENCHMARK_SYMBOL = "SPY"`

Change this constant in `app/analysis/portfolio.py` to use another Yahoo Finance benchmark symbol.

## Portfolio beta

Daily current-weight portfolio returns are regressed against benchmark returns.

`beta_p = Cov(Rp, Rm) / Var(Rm)`

Current portfolio signed weights are normalized by total gross exposure.

- LONG weight: positive
- SHORT weight: negative

A SHORT position in a positive-beta asset therefore contributes negatively to portfolio beta.

## Alpha

Daily regression alpha is:

`alpha_daily = mean(Rp) - beta_p * mean(Rm)`

The console reports:

`alpha_annualized = alpha_daily * 252`

This is a historical regression intercept, not a forecast of future excess return.

## Correlation and R-squared

- Correlation measures linear co-movement with the benchmark.
- R-squared is correlation squared in this one-factor regression and estimates the fraction of portfolio return variance explained by the benchmark factor.

## Systematic and idiosyncratic volatility

`systematic_vol = abs(beta_p) * benchmark_vol`

Idiosyncratic volatility is the annualized standard deviation of OLS residual returns.

These are volatility measures, not loss limits.

## Beta contribution

Each aggregated Yahoo market factor has:

`beta_contribution_i = signed_weight_i * beta_i`

The contributions sum to portfolio beta when calculated over the same aligned sample.

This makes the report LONG/SHORT-aware and identifies which positions add to or hedge benchmark exposure.

## History and coverage

Stage 2.4 uses the same extended-risk conventions as Stage 2.3.1:

- maximum 756 daily observations (~3 years)
- minimum 504 common observations (~2 years)
- assets with insufficient history are excluded
- risk coverage reports the percentage of gross exposure represented

## Limitations

- The benchmark is a single-factor model.
- Current portfolio weights are applied retrospectively.
- Alpha and beta are historical estimates and can change materially over time.
- Explicit FX factors are not yet modeled.
- SPY is a configurable default, not necessarily the economically optimal benchmark for every multi-asset portfolio.
