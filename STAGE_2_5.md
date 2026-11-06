# Stage 2.5 — Multi-Factor Risk

Stage 2.5 extends the single-benchmark Stage 2.4 model to a configurable multi-factor OLS regression.

## Default factor proxies

The command-line report currently uses:

- `US_MARKET = SPY`
- `EUROPE = VGK`
- `GOLD = GLD`
- `TECH = QQQ`

These are configurable liquid-market proxies, not canonical academic factors and not a claim that this is the only correct factor set.

Edit `MULTI_FACTOR_SYMBOLS` in `app/analysis/portfolio.py` to change the factor model.

## Model

The current signed-weight portfolio return is modeled as:

`Rp,t = alpha + beta_1 F_1,t + ... + beta_k F_k,t + epsilon_t`

The coefficients are estimated simultaneously by ordinary least squares.

This is important because the factors can be correlated. The coefficient for `TECH`, for example, is the technology exposure after controlling for the other factors in the regression.

## LONG / SHORT

Current exposures remain normalized by total gross exposure:

- LONG position -> positive signed weight
- SHORT position -> negative signed weight

For every factor:

`factor_beta_contribution_i = signed_weight_i * asset_factor_beta_i`

The contributions sum to the corresponding portfolio factor beta when calculated on the same aligned sample.

A SHORT can therefore reduce one factor exposure while increasing another, depending on its historical multivariate betas.

## Outputs

The Stage 2.5 report includes:

- annualized multi-factor alpha
- R-squared
- adjusted R-squared
- portfolio volatility
- systematic volatility from fitted multi-factor returns
- idiosyncratic/residual volatility
- condition number
- portfolio beta for each factor
- beta reconstructed from position-level contributions
- Variance Inflation Factor (VIF) for each factor
- pairwise factor correlations
- position-level beta contribution for each factor

## Adjusted R-squared

Adjusted R-squared penalizes the addition of factors that do not materially improve explanatory power.

It is useful when comparing the 4-factor model with the Stage 2.4 single-factor SPY model.

## Multicollinearity

Factor proxies can overlap substantially. For example, broad US equities and technology equities are often correlated.

Two diagnostics are reported:

### VIF

For factor j:

`VIF_j = 1 / (1 - R2_j)`

where `R2_j` is obtained by regressing factor j against the other factors.

Interpretation used by the console:

- around 1: little overlap
- above 5: elevated multicollinearity
- above 10: severe multicollinearity

High VIF does not invalidate the portfolio fit, but it means the individual factor beta can be unstable and should not be over-interpreted.

### Condition number

The condition number is calculated on standardized factor returns.

A large value is another warning that the factor matrix is close to collinear.

## Risk decomposition

`systematic_volatility` is the annualized standard deviation of the OLS fitted portfolio returns.

`idiosyncratic_volatility` is the annualized standard deviation of regression residuals.

Because OLS fitted values and residuals are orthogonal in-sample, their variances approximately reconstruct total portfolio variance.

## History and coverage

The model uses the same extended-risk conventions as Stages 2.3.1 and 2.4:

- maximum 756 daily observations (~3 years)
- minimum 504 common observations (~2 years)
- insufficient-history portfolio assets are excluded
- risk coverage reports the represented percentage of gross exposure

## Limitations

- This is a proxy factor model, not a Fama-French or Barra implementation.
- The factor set is user-configurable and model results depend materially on factor choice.
- Factor betas are historical estimates, not forecasts.
- Current portfolio weights are applied retrospectively.
- High correlation between factors can make individual coefficients unstable.
- Explicit FX factors are not yet modeled.
- Factor ETFs can embed their own sector/country exposures and therefore are imperfect pure factors.
