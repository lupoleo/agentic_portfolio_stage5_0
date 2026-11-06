# Stage 2.5 - Excel Reporting Layer

The analytical model is unchanged. This addition writes the completed Portfolio Analysis back into the original Fineco workbook after the console report has been produced.

## Output file

The application updates:

`data/input/portafoglio-export.xlsx`

The original Fineco worksheet(s) are preserved. Portfolio Analysis owns only the following worksheets and recreates them on every run:

- `PA_Overview`
- `PA_Positions`
- `PA_CovRisk`
- `PA_Benchmark`
- `PA_MultiFactor`
- `PA_TailRisk`
- `PA_Legenda_IT`
- `PA_Legend_EN`

## Sheet contents

### PA_Overview
Portfolio-level KPI summary: exposure, concentration, covariance-aware risk, benchmark model, multi-factor model and historical VaR/CVaR.

### PA_Positions
Position-level technical analysis, scoring and Stage 2.1 risk load. Fineco lines remain distinct.

### PA_CovRisk
Covariance-aware market-factor risk contribution plus the full correlation matrix. Duplicate Fineco lines resolving to the same Yahoo symbol are aggregated at market-factor level.

### PA_Benchmark
Single-benchmark Stage 2.4 results and position-level beta contribution.

### PA_MultiFactor
Stage 2.5 OLS model, factor betas, VIF, factor correlation matrix and position-level factor beta contribution.

### PA_TailRisk
1-day and 10-day Historical VaR/CVaR, tail observation count, robustness classification and risk coverage.

### PA_Legenda_IT / PA_Legend_EN
Bilingual documentation of formulas, assumptions, conventions, interpretation and model limitations.

## Pivot tables

No PivotTable is created in this version. The analytical engine already performs the required grouping and aggregation, and PivotTables would duplicate those calculations. Detailed outputs are stored as native Excel Tables, which support filtering and sorting directly in Excel.

## LONG / SHORT

All Excel reports use the same LONG/SHORT conventions as the console model. No financial calculation has been changed by the reporting layer.

## Workbook safety

The workbook is written using a temporary file and then atomically replaces the destination. If the workbook is open in Excel on Windows, the application prints a warning asking the user to close it and rerun the command.
