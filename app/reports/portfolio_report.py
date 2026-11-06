from __future__ import annotations

from pathlib import Path
import os
import tempfile
from typing import TYPE_CHECKING

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

if TYPE_CHECKING:
    from app.analysis.portfolio import AnalyzedPosition
    from app.analysis.risk import (
        PortfolioRiskSummary,
        PortfolioCovarianceRiskSummary,
        PortfolioHistoricalTailRiskSummary,
    )
    from app.analysis.benchmark import PortfolioBenchmarkRiskSummary
    from app.analysis.multifactor import PortfolioMultiFactorSummary


REPORT_SHEETS = (
    "PA_Overview",
    "PA_Positions",
    "PA_CovRisk",
    "PA_Benchmark",
    "PA_MultiFactor",
    "PA_TailRisk",
    "PA_Legenda_IT",
    "PA_Legend_EN",
)

DARK_BLUE = "17365D"
MID_BLUE = "2F75B5"
LIGHT_BLUE = "D9EAF7"
LIGHT_GREEN = "E2F0D9"
LIGHT_YELLOW = "FFF2CC"
LIGHT_RED = "FCE4D6"
WHITE = "FFFFFF"
BLACK = "000000"
GREY = "666666"
THIN_GREY = Side(style="thin", color="D9E1F2")


# -----------------------------------------------------------------------------
# Generic workbook helpers
# -----------------------------------------------------------------------------


def _safe_sheet_title(name: str) -> str:
    return name[:31]


def _delete_previous_report_sheets(workbook) -> None:
    for name in REPORT_SHEETS:
        if name in workbook.sheetnames:
            del workbook[name]


def _title(ws, title: str, subtitle: str | None = None, width: int = 8) -> int:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width)
    cell = ws.cell(1, 1, title)
    cell.fill = PatternFill("solid", fgColor=DARK_BLUE)
    cell.font = Font(color=WHITE, bold=True, size=15)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24

    if subtitle:
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=width)
        sub = ws.cell(2, 1, subtitle)
        sub.font = Font(color=GREY, italic=True, size=9)
        sub.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[2].height = 30
        return 4
    return 3


def _section(ws, row: int, title: str, width: int = 4) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = ws.cell(row, 1, title)
    cell.fill = PatternFill("solid", fgColor=MID_BLUE)
    cell.font = Font(color=WHITE, bold=True)
    cell.alignment = Alignment(horizontal="left")
    return row + 1


def _write_key_values(ws, row: int, values: list[tuple[str, object, str | None]]) -> int:
    for label, value, number_format in values:
        ws.cell(row, 1, label)
        ws.cell(row, 1).font = Font(bold=True)
        ws.cell(row, 2, value)
        if number_format:
            ws.cell(row, 2).number_format = number_format
        row += 1
    return row


def _make_table(ws, start_row: int, headers: list[str], rows: list[list[object]], name: str) -> int:
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(start_row, col, header)
        cell.fill = PatternFill("solid", fgColor=DARK_BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN_GREY)

    for r_index, row_values in enumerate(rows, start=start_row + 1):
        for c_index, value in enumerate(row_values, start=1):
            cell = ws.cell(r_index, c_index, value)
            cell.border = Border(bottom=THIN_GREY)
            cell.alignment = Alignment(vertical="top")

    end_row = start_row + max(len(rows), 1)
    end_col = len(headers)

    if rows:
        ref = f"A{start_row}:{get_column_letter(end_col)}{end_row}"
        table = Table(displayName=name, ref=ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        ws.add_table(table)

    return end_row + 2


def _format_columns(ws, widths: dict[int, float]) -> None:
    for index, width in widths.items():
        ws.column_dimensions[get_column_letter(index)].width = width


def _freeze_filter_style(ws, row: int) -> None:
    ws.freeze_panes = f"A{row}"
    ws.sheet_view.showGridLines = False


def _apply_number_formats(ws, columns: dict[int, str], first_data_row: int, last_row: int) -> None:
    for column_index, fmt in columns.items():
        for row in range(first_data_row, last_row + 1):
            ws.cell(row, column_index).number_format = fmt


def _atomic_save(workbook, destination: Path) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=destination.stem + "_tmp_",
        suffix=".xlsx",
        dir=destination.parent,
    )
    os.close(fd)

    try:
        workbook.save(temp_name)
        os.replace(temp_name, destination)
    except Exception:
        if os.path.exists(temp_name):
            os.remove(temp_name)
        raise


# -----------------------------------------------------------------------------
# Report sheets
# -----------------------------------------------------------------------------


def _build_overview(
    workbook,
    analyzed_positions,
    portfolio_risk,
    covariance_risk,
    benchmark_risk,
    multifactor_risk,
    tail_risk_1d,
    tail_risk_10d,
) -> None:
    ws = workbook.create_sheet("PA_Overview")
    row = _title(
        ws,
        "Portfolio Analysis 2.5 - Overview",
        "Automated analysis appended to the original Fineco workbook. "
        "Current portfolio weights are used for historical risk and factor models.",
        width=6,
    )

    row = _section(ws, row, "Portfolio exposure and concentration", 4)
    row = _write_key_values(
        ws,
        row,
        [
            ("Analyzed positions", portfolio_risk.positions, "0"),
            ("Long positions", portfolio_risk.long_positions, "0"),
            ("Short positions", portfolio_risk.short_positions, "0"),
            ("Long exposure (EUR)", portfolio_risk.long_exposure_eur, '#,##0.00;[Red](#,##0.00);-'),
            ("Short exposure (EUR)", portfolio_risk.short_exposure_eur, '#,##0.00;[Red](#,##0.00);-'),
            ("Gross exposure (EUR)", portfolio_risk.gross_exposure_eur, '#,##0.00;[Red](#,##0.00);-'),
            ("Net exposure (EUR)", portfolio_risk.net_exposure_eur, '#,##0.00;[Red](#,##0.00);-'),
            ("Net / Gross", portfolio_risk.net_to_gross_pct / 100, '0.00%'),
            ("Largest position", portfolio_risk.largest_position_pct / 100, '0.00%'),
            ("Top 5 concentration", portfolio_risk.top5_concentration_pct / 100, '0.00%'),
            ("HHI", portfolio_risk.hhi, '0.0000'),
            ("Effective positions", portfolio_risk.effective_positions, '0.00'),
        ],
    )

    row += 1
    row = _section(ws, row, "Covariance-aware portfolio risk", 4)
    row = _write_key_values(
        ws,
        row,
        [
            ("Risk coverage", covariance_risk.risk_coverage_pct / 100, '0.00%'),
            ("Common observations", covariance_risk.observations, '0'),
            ("Portfolio volatility", covariance_risk.portfolio_volatility_pct / 100, '0.00%'),
            ("Weighted standalone volatility", covariance_risk.weighted_standalone_volatility_pct / 100, '0.00%'),
            ("Diversification ratio", covariance_risk.diversification_ratio, '0.00x'),
            ("Excluded symbols", ", ".join(covariance_risk.excluded_symbols) or "None", None),
        ],
    )

    row += 1
    row = _section(ws, row, f"Benchmark risk ({benchmark_risk.benchmark_symbol})", 4)
    row = _write_key_values(
        ws,
        row,
        [
            ("Portfolio beta", benchmark_risk.portfolio_beta, '0.000'),
            ("Annualized alpha", benchmark_risk.alpha_annualized_pct / 100, '0.00%'),
            ("Correlation", benchmark_risk.correlation, '0.000'),
            ("R-squared", benchmark_risk.r_squared, '0.000'),
            ("Systematic volatility", benchmark_risk.systematic_volatility_pct / 100, '0.00%'),
            ("Idiosyncratic volatility", benchmark_risk.idiosyncratic_volatility_pct / 100, '0.00%'),
        ],
    )

    row += 1
    row = _section(ws, row, "Multi-factor model", 4)
    factor_text = ", ".join(
        f"{name}={symbol}"
        for name, symbol in zip(multifactor_risk.factor_names, multifactor_risk.factor_symbols)
    )
    row = _write_key_values(
        ws,
        row,
        [
            ("Factors", factor_text, None),
            ("Annualized alpha", multifactor_risk.alpha_annualized_pct / 100, '0.00%'),
            ("R-squared", multifactor_risk.r_squared, '0.000'),
            ("Adjusted R-squared", multifactor_risk.adjusted_r_squared, '0.000'),
            ("Systematic volatility", multifactor_risk.systematic_volatility_pct / 100, '0.00%'),
            ("Idiosyncratic volatility", multifactor_risk.idiosyncratic_volatility_pct / 100, '0.00%'),
            ("Condition number", multifactor_risk.condition_number, '0.00'),
        ],
    )

    row += 1
    row = _section(ws, row, "Historical tail risk", 6)
    tail_rows = []
    for risk in (tail_risk_1d, tail_risk_10d):
        for level in risk.levels:
            tail_rows.append([
                risk.horizon_days,
                level.confidence_level_pct / 100,
                level.var_pct / 100,
                level.cvar_pct / 100,
                level.var_eur,
                level.cvar_eur,
            ])
    table_start = row
    _make_table(
        ws,
        table_start,
        ["Horizon (days)", "Confidence", "VaR %", "CVaR %", "VaR EUR", "CVaR EUR"],
        tail_rows,
        "PAOverviewTailRisk",
    )
    _apply_number_formats(
        ws,
        {2: '0%', 3: '0.00%', 4: '0.00%', 5: '#,##0.00;[Red](#,##0.00);-', 6: '#,##0.00;[Red](#,##0.00);-'},
        table_start + 1,
        table_start + len(tail_rows),
    )

    _format_columns(ws, {1: 34, 2: 30, 3: 16, 4: 16, 5: 18, 6: 18})
    ws.sheet_view.showGridLines = False



def _build_positions(workbook, analyzed_positions, portfolio_risk) -> None:
    ws = workbook.create_sheet("PA_Positions")
    start = _title(
        ws,
        "Position-level Technical, Scoring and Risk Load",
        "Position-level rows remain separate even when multiple Fineco lines resolve to the same Yahoo market factor.",
        width=27,
    )

    risk_by_key: dict[tuple[str, str, float], list] = {}
    for metric in portfolio_risk.position_metrics:
        key = (metric.yahoo_symbol, metric.direction, round(metric.exposure_eur, 6))
        risk_by_key.setdefault(key, []).append(metric)

    rows = []
    for item in analyzed_positions:
        p = item.position
        t = item.technical
        s = item.scores
        key = (item.yahoo_symbol, p.direction, round(abs(p.market_value_eur), 6))
        candidates = risk_by_key.get(key, [])
        metric = candidates.pop(0) if candidates else None

        rows.append([
            item.yahoo_symbol,
            p.name,
            p.direction,
            p.market_value_eur,
            item.weight_pct / 100,
            p.pnl_percent / 100,
            item.price_gap_pct / 100 if item.price_gap_comparable else None,
            t.current_price if item.price_gap_comparable else None,
            t.performance_1y_pct / 100,
            t.sma20,
            t.sma50,
            t.distance_from_sma20_pct / 100,
            t.distance_from_sma50_pct / 100,
            t.rsi14,
            t.volatility_20d_pct / 100,
            t.relative_volume,
            t.trend,
            s.momentum_score,
            s.risk_score,
            s.position_alignment,
            metric.net_weight_pct / 100 if metric else None,
            metric.volatility_load if metric else None,
            metric.technical_risk_load if metric else None,
            item.market_data_symbol or item.yahoo_symbol,
            item.history_method,
            item.leverage_multiplier,
            item.instrument_reference_id,
        ])

    headers = [
        "Symbol", "Asset", "Dir", "Value EUR", "Weight", "P/L", "PxGap",
        "Direct Provider Price", "1Y Performance", "SMA20", "SMA50", "vs SMA20", "vs SMA50",
        "RSI14", "Vol20 Ann.", "RVOL", "Trend", "Momentum Score", "Technical Risk Score",
        "Alignment", "Signed Weight", "Vol Load", "Tech Risk Load",
        "Source Symbol", "History Method", "Leverage", "Reference ID",
    ]
    table_start = start
    _make_table(ws, table_start, headers, rows, "PAPositionsTable")
    last = table_start + len(rows)
    _apply_number_formats(
        ws,
        {
            4: '#,##0.00;[Red](#,##0.00);-',
            5: '0.00%', 6: '0.00%;[Red](0.00%);-', 7: '0.00%;[Red](0.00%);-',
            8: '#,##0.0000', 9: '0.00%;[Red](0.00%);-', 10: '#,##0.0000', 11: '#,##0.0000',
            12: '0.00%;[Red](0.00%);-', 13: '0.00%;[Red](0.00%);-', 14: '0.0',
            15: '0.00%', 16: '0.00x', 18: '0.0', 19: '0.0', 21: '0.00%;[Red](0.00%);-',
            22: '0.00', 23: '0.00', 26: '0.0x',
        },
        table_start + 1,
        last,
    )
    _format_columns(ws, {
        1: 13, 2: 34, 3: 9, 4: 15, 5: 11, 6: 11, 7: 11, 8: 14, 9: 14,
        10: 12, 11: 12, 12: 12, 13: 12, 14: 10, 15: 12, 16: 10, 17: 11,
        18: 15, 19: 18, 20: 13, 21: 13, 22: 12, 23: 14,
        24: 15, 25: 18, 26: 10, 27: 34,
    })
    _freeze_filter_style(ws, table_start + 1)



def _build_covariance(workbook, analyzed_positions, covariance_risk) -> None:
    ws = workbook.create_sheet("PA_CovRisk")
    start = _title(
        ws,
        "Covariance-Aware Risk and Correlations",
        "Market-factor rows are aggregated by Yahoo symbol. Negative component risk indicates a hedge contribution under the covariance model.",
        width=10,
    )

    name_by_symbol = {item.yahoo_symbol: item.position.name for item in analyzed_positions}
    rows = []
    for metric in sorted(
        covariance_risk.asset_metrics,
        key=lambda x: abs(x.risk_contribution_pct),
        reverse=True,
    ):
        rows.append([
            metric.yahoo_symbol,
            name_by_symbol.get(metric.yahoo_symbol, ""),
            metric.direction,
            metric.gross_exposure_eur,
            metric.signed_exposure_eur,
            metric.signed_weight_pct / 100,
            metric.annualized_volatility_pct / 100,
            metric.marginal_risk,
            metric.component_risk_pct_points / 100,
            metric.risk_contribution_pct / 100,
        ])

    table_start = start
    row = _make_table(
        ws,
        table_start,
        ["Symbol", "Asset", "Dir", "Gross Exposure EUR", "Signed Exposure EUR", "Signed Weight", "Asset Vol Ann.", "Marginal Risk", "Component Risk", "Risk Contribution"],
        rows,
        "PACovRiskTable",
    )
    _apply_number_formats(
        ws,
        {4: '#,##0.00;[Red](#,##0.00);-', 5: '#,##0.00;[Red](#,##0.00);-', 6: '0.00%;[Red](0.00%);-', 7: '0.00%', 8: '0.0000', 9: '0.00%;[Red](0.00%);-', 10: '0.00%;[Red](0.00%);-'},
        table_start + 1,
        table_start + len(rows),
    )

    row = _section(ws, row, "Correlation matrix", max(len(covariance_risk.correlation_matrix.columns) + 1, 4))
    corr = covariance_risk.correlation_matrix
    symbols = list(corr.columns)
    matrix_headers = ["Symbol"] + symbols
    matrix_rows = [[left] + [float(corr.loc[left, right]) for right in symbols] for left in symbols]
    matrix_start = row
    _make_table(ws, matrix_start, matrix_headers, matrix_rows, "PACorrelationMatrix")
    _apply_number_formats(ws, {i: '0.000' for i in range(2, len(matrix_headers) + 1)}, matrix_start + 1, matrix_start + len(matrix_rows))

    _format_columns(ws, {1: 14, 2: 34, 3: 9, 4: 18, 5: 18, 6: 13, 7: 14, 8: 14, 9: 14, 10: 16})
    for i in range(11, len(matrix_headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 11
    _freeze_filter_style(ws, table_start + 1)



def _build_benchmark(workbook, analyzed_positions, benchmark_risk) -> None:
    ws = workbook.create_sheet("PA_Benchmark")
    row = _title(
        ws,
        f"Benchmark Risk - {benchmark_risk.benchmark_symbol}",
        "Single-factor historical OLS model. Alpha and beta are historical estimates, not forecasts.",
        width=9,
    )

    row = _section(ws, row, "Portfolio-level benchmark metrics", 4)
    row = _write_key_values(ws, row, [
        ("Risk coverage", benchmark_risk.risk_coverage_pct / 100, '0.00%'),
        ("Observations", benchmark_risk.observations, '0'),
        ("Portfolio beta", benchmark_risk.portfolio_beta, '0.000'),
        ("Beta from components", benchmark_risk.beta_from_components, '0.000'),
        ("Annualized alpha", benchmark_risk.alpha_annualized_pct / 100, '0.00%'),
        ("Correlation", benchmark_risk.correlation, '0.000'),
        ("R-squared", benchmark_risk.r_squared, '0.000'),
        ("Benchmark volatility", benchmark_risk.benchmark_volatility_pct / 100, '0.00%'),
        ("Portfolio volatility", benchmark_risk.portfolio_volatility_pct / 100, '0.00%'),
        ("Systematic volatility", benchmark_risk.systematic_volatility_pct / 100, '0.00%'),
        ("Idiosyncratic volatility", benchmark_risk.idiosyncratic_volatility_pct / 100, '0.00%'),
    ])

    row += 1
    row = _section(ws, row, "Position beta contribution", 9)
    name_by_symbol = {item.yahoo_symbol: item.position.name for item in analyzed_positions}
    rows = []
    for metric in sorted(benchmark_risk.asset_metrics, key=lambda x: abs(x.beta_contribution), reverse=True):
        rows.append([
            metric.yahoo_symbol,
            name_by_symbol.get(metric.yahoo_symbol, ""),
            metric.direction,
            metric.signed_weight_pct / 100,
            metric.beta,
            metric.beta_contribution,
            metric.alpha_annualized_pct / 100,
            metric.correlation,
            metric.r_squared,
        ])
    table_start = row
    _make_table(
        ws,
        table_start,
        ["Symbol", "Asset", "Dir", "Signed Weight", "Beta", "Beta Contribution", "Alpha Ann.", "Correlation", "R-squared"],
        rows,
        "PABenchmarkTable",
    )
    _apply_number_formats(ws, {4: '0.00%;[Red](0.00%);-', 5: '0.000', 6: '0.000;[Red](0.000);-', 7: '0.00%;[Red](0.00%);-', 8: '0.000', 9: '0.000'}, table_start + 1, table_start + len(rows))
    _format_columns(ws, {1: 14, 2: 34, 3: 9, 4: 14, 5: 11, 6: 18, 7: 13, 8: 13, 9: 12})
    ws.sheet_view.showGridLines = False



def _build_multifactor(workbook, analyzed_positions, multifactor_risk) -> None:
    ws = workbook.create_sheet("PA_MultiFactor")
    row = _title(
        ws,
        "Multi-Factor Risk Model",
        "Simultaneous OLS regression. Factor coefficients are conditional on the other factors; high VIF indicates unstable individual coefficients.",
        width=10,
    )

    row = _section(ws, row, "Portfolio-level multi-factor metrics", 4)
    row = _write_key_values(ws, row, [
        ("Risk coverage", multifactor_risk.risk_coverage_pct / 100, '0.00%'),
        ("Observations", multifactor_risk.observations, '0'),
        ("Annualized alpha", multifactor_risk.alpha_annualized_pct / 100, '0.00%'),
        ("R-squared", multifactor_risk.r_squared, '0.000'),
        ("Adjusted R-squared", multifactor_risk.adjusted_r_squared, '0.000'),
        ("Portfolio volatility", multifactor_risk.portfolio_volatility_pct / 100, '0.00%'),
        ("Systematic volatility", multifactor_risk.systematic_volatility_pct / 100, '0.00%'),
        ("Idiosyncratic volatility", multifactor_risk.idiosyncratic_volatility_pct / 100, '0.00%'),
        ("Factor condition number", multifactor_risk.condition_number, '0.00'),
    ])

    row += 1
    row = _section(ws, row, "Factor exposures and multicollinearity", 5)
    factor_rows = [[m.name, m.symbol, m.beta, m.beta_from_assets, m.vif] for m in multifactor_risk.factor_metrics]
    factor_start = row
    row = _make_table(ws, factor_start, ["Factor", "Ticker", "Beta", "From Assets", "VIF"], factor_rows, "PAFactorMetrics")
    _apply_number_formats(ws, {3: '0.000', 4: '0.000', 5: '0.00'}, factor_start + 1, factor_start + len(factor_rows))

    row = _section(ws, row, "Factor correlation matrix", len(multifactor_risk.factor_names) + 1)
    corr = multifactor_risk.factor_correlation_matrix
    factor_names = list(corr.columns)
    matrix_headers = ["Factor"] + factor_names
    matrix_rows = [[left] + [float(corr.loc[left, right]) for right in factor_names] for left in factor_names]
    matrix_start = row
    row = _make_table(ws, matrix_start, matrix_headers, matrix_rows, "PAFactorCorrelation")
    _apply_number_formats(ws, {i: '0.000' for i in range(2, len(matrix_headers) + 1)}, matrix_start + 1, matrix_start + len(matrix_rows))

    row = _section(ws, row, "Asset factor beta contribution", 4 + len(multifactor_risk.factor_names))
    name_by_symbol = {item.yahoo_symbol: item.position.name for item in analyzed_positions}
    headers = ["Symbol", "Asset", "Dir", "Signed Weight"] + [f"{name} Contribution" for name in multifactor_risk.factor_names]
    asset_rows = []
    for metric in sorted(
        multifactor_risk.asset_metrics,
        key=lambda x: sum(abs(v) for v in x.factor_beta_contributions),
        reverse=True,
    ):
        asset_rows.append([
            metric.yahoo_symbol,
            name_by_symbol.get(metric.yahoo_symbol, ""),
            metric.direction,
            metric.signed_weight_pct / 100,
            *metric.factor_beta_contributions,
        ])
    asset_start = row
    _make_table(ws, asset_start, headers, asset_rows, "PAMultiFactorContribution")
    formats = {4: '0.00%;[Red](0.00%);-'}
    formats.update({i: '0.000;[Red](0.000);-' for i in range(5, len(headers) + 1)})
    _apply_number_formats(ws, formats, asset_start + 1, asset_start + len(asset_rows))

    _format_columns(ws, {1: 15, 2: 34, 3: 9, 4: 14, 5: 18, 6: 18, 7: 18, 8: 18})
    ws.sheet_view.showGridLines = False



def _build_tail_risk(workbook, tail_risk_1d, tail_risk_10d) -> None:
    ws = workbook.create_sheet("PA_TailRisk")
    row = _title(
        ws,
        "Historical VaR and CVaR / Expected Shortfall",
        "Historical simulation uses current signed LONG/SHORT weights normalized by gross exposure. VaR is not a maximum possible loss.",
        width=11,
    )

    rows = []
    for risk in (tail_risk_1d, tail_risk_10d):
        for level in risk.levels:
            if level.tail_observations < 5:
                robustness = "VERY LOW"
            elif level.tail_observations < 10:
                robustness = "LOW"
            elif level.tail_observations < 20:
                robustness = "MODERATE"
            else:
                robustness = "OK"
            rows.append([
                risk.horizon_days,
                risk.observations,
                risk.risk_coverage_pct / 100,
                risk.mean_return_pct / 100,
                risk.best_return_pct / 100,
                risk.worst_return_pct / 100,
                level.confidence_level_pct / 100,
                level.var_pct / 100,
                level.cvar_pct / 100,
                level.var_eur,
                level.cvar_eur,
                level.tail_observations,
                robustness,
                ", ".join(risk.excluded_symbols) or "None",
            ])

    table_start = row
    _make_table(
        ws,
        table_start,
        ["Horizon Days", "Observations", "Risk Coverage", "Mean Return", "Best Return", "Worst Return", "Confidence", "VaR %", "CVaR %", "VaR EUR", "CVaR EUR", "Tail N", "Robustness", "Excluded Symbols"],
        rows,
        "PATailRiskTable",
    )
    _apply_number_formats(ws, {3: '0.00%', 4: '0.00%;[Red](0.00%);-', 5: '0.00%;[Red](0.00%);-', 6: '0.00%;[Red](0.00%);-', 7: '0%', 8: '0.00%', 9: '0.00%', 10: '#,##0.00;[Red](#,##0.00);-', 11: '#,##0.00;[Red](#,##0.00);-'}, table_start + 1, table_start + len(rows))
    _format_columns(ws, {1: 13, 2: 14, 3: 14, 4: 13, 5: 13, 6: 13, 7: 12, 8: 12, 9: 12, 10: 16, 11: 16, 12: 10, 13: 13, 14: 28})
    _freeze_filter_style(ws, table_start + 1)


# -----------------------------------------------------------------------------
# Bilingual assumptions / legend sheets
# -----------------------------------------------------------------------------


def _legend_rows_it() -> list[list[str]]:
    return [
        ["SEZIONE", "VOCE", "DEFINIZIONE / FORMULA / ASSUNZIONE", "INTERPRETAZIONE / LIMITI"],
        ["Dati", "Fonte prezzi", "Yahoo Finance tramite yfinance; prezzi storici adjusted quando forniti dal provider.", "La qualità dipende dal provider e dal corretto mapping ticker Fineco->Yahoo."],
        ["Dati", "Finestra tecnica", "Massimo 252 sedute (~1 anno).", "Usata per performance 1Y e indicatori tecnici; separata dalla finestra risk."],
        ["Dati", "Finestra risk", "Massimo 756 osservazioni (~3 anni); minimo 504 osservazioni comuni (~2 anni).", "Asset con storico insufficiente sono esclusi e la Risk Coverage viene riportata."],
        ["Posizioni", "Dir", "LONG se quantità > 0; SHORT se quantità < 0; FLAT se quantità = 0.", "La direzione deriva dalla quantità, non dal segno del market value Fineco."],
        ["Posizioni", "Gross Exposure", "Somma dei valori assoluti delle esposizioni.", "LONG e SHORT non si compensano nel gross exposure."],
        ["Posizioni", "Net Exposure", "Long Exposure - Short Exposure.", "Misura la direzione netta del portafoglio."],
        ["Posizioni", "Gross Weight", "|Exposure_i| / Gross Exposure.", "Sempre non negativo; usato per concentrazione."],
        ["Posizioni", "Signed Weight / NetW", "Signed Exposure_i / Gross Exposure.", "LONG positivo, SHORT negativo; usato nei modelli di rischio."],
        ["Tecnica", "SMA20 / SMA50", "Media mobile semplice rispettivamente a 20 e 50 sedute.", "Indicatori di struttura del trend, non segnali automatici."],
        ["Tecnica", "vsSMA20 / vsSMA50", "Prezzo corrente / SMA - 1.", "Distanza percentuale del prezzo dalla media mobile."],
        ["Tecnica", "RSI14", "Relative Strength Index a 14 sedute con smoothing tipo Wilder; scala 0-100.", ">70 tradizionalmente forte/overbought; <30 debole/oversold; non è un segnale automatico."],
        ["Tecnica", "Vol20", "Deviazione standard dei rendimenti giornalieri delle ultime 20 sedute x sqrt(252).", "Volatilità storica annualizzata recente."],
        ["Tecnica", "RVOL", "Volume ultima seduta / volume medio delle 20 sedute precedenti.", ">1 indica volume sopra la media recente."],
        ["Tecnica", "Trend", "BULLISH: Price>SMA20>SMA50; BEARISH: Price<SMA20<SMA50; altrimenti NEUTRAL.", "Classificazione deterministica della struttura del prezzo."],
        ["Scoring", "Momentum Score", "0-100: Trend 30, vsSMA20 20, vsSMA50 20, RSI14 20, RVOL 10.", "Più alto = momentum tecnico più positivo secondo regole euristiche del progetto."],
        ["Scoring", "Technical Risk Score", "0-100: Volatilità 60, estensione da SMA20 20, estremi RSI 20.", "Più alto = maggiore rischio tecnico; non è VaR e non include da solo correlazioni o size."],
        ["Scoring", "Alignment", "LONG+BULLISH o SHORT+BEARISH = ALIGNED; combinazione opposta = AGAINST; trend neutro = NEUTRAL.", "Misura coerenza tra direzione della posizione e trend tecnico."],
        ["Concentrazione", "HHI", "Somma dei quadrati dei gross weights.", "Più alto = maggiore concentrazione."],
        ["Concentrazione", "Effective Positions", "1 / HHI.", "Numero equivalente di posizioni equi-pesate."],
        ["Risk 2.1", "VolLoad", "Gross Weight x Vol20.", "Misura euristica, non covariance-aware."],
        ["Risk 2.1", "TechLoad", "Gross Weight x Technical Risk Score.", "Carico di rischio tecnico ponderato per esposizione."],
        ["Risk 2.2", "Portfolio Volatility", "sqrt(w' Sigma w), con w signed e Sigma matrice di covarianza annualizzata.", "Volatilità storica del portafoglio rispetto al gross exposure."],
        ["Risk 2.2", "Component Risk", "w_i x marginal risk_i.", "Può essere negativo: la posizione può agire da hedge."],
        ["Risk 2.2", "Risk Contribution", "Component Risk_i / Portfolio Volatility.", "I contributi sommano a 100% (salvo arrotondamenti)."],
        ["Risk 2.2", "Diversification Ratio", "Weighted standalone volatility / Portfolio volatility.", ">1 indica beneficio di diversificazione."],
        ["Risk 2.3", "Historical VaR", "Quantile storico della perdita alla confidenza scelta; riportato come numero positivo.", "Non è la perdita massima possibile."],
        ["Risk 2.3", "CVaR / Expected Shortfall", "Perdita media nelle osservazioni uguali o peggiori del VaR.", "Descrive la severità della coda oltre il VaR."],
        ["Risk 2.3", "10D VaR/CVaR", "Rendimenti rolling realmente composti a 10 giorni.", "Non usa scaling sqrt(time)."],
        ["Risk 2.3", "Tail N", "Numero di osservazioni empiriche nella coda.", "<5 very low, 5-9 low, 10-19 moderate robustness."],
        ["Benchmark 2.4", "Beta", "Cov(Rp,Rm)/Var(Rm) rispetto al benchmark configurato (default SPY).", "Sensibilità storica al benchmark; SHORT positive-beta riduce il beta del portafoglio."],
        ["Benchmark 2.4", "Alpha", "Intercetta OLS giornaliera annualizzata linearmente x252.", "Stima storica, non rendimento futuro atteso."],
        ["Benchmark 2.4", "R-squared", "Quadrato della correlazione nel modello a un fattore.", "Quota di varianza spiegata dal benchmark."],
        ["Benchmark 2.4", "Systematic Vol", "|beta| x volatilità benchmark.", "Componente di volatilità associata al benchmark nel modello a un fattore."],
        ["Benchmark 2.4", "Idiosyncratic Vol", "Deviazione standard annualizzata dei residui OLS.", "Rischio non spiegato dal benchmark."],
        ["Benchmark 2.4", "Beta Contribution", "Signed Weight_i x Beta_i.", "Somma dei contributi = Portfolio Beta sulla stessa finestra."],
        ["Multi-factor 2.5", "Modello", "Rp = alpha + beta1 F1 + ... + betak Fk + epsilon.", "Coefficienti stimati simultaneamente; ogni beta è condizionato sugli altri fattori."],
        ["Multi-factor 2.5", "Fattori default", "US_MARKET=SPY, EUROPE=VGK, GOLD=GLD, TECH=QQQ.", "Proxy configurabili, non fattori accademici puri."],
        ["Multi-factor 2.5", "Adjusted R-squared", "R² corretto per il numero di fattori.", "Penalizza fattori che non aggiungono sufficiente potere esplicativo."],
        ["Multi-factor 2.5", "VIF", "1/(1-R²_j), dove il fattore j è regressato sugli altri fattori.", ">5 multicollinearità elevata; >10 severa; beta individuali possono essere instabili."],
        ["Multi-factor 2.5", "Condition Number", "Numero di condizionamento della matrice dei fattori standardizzati.", "Valori elevati segnalano quasi-collinearità."],
        ["Multi-factor 2.5", "Factor Beta Contribution", "Signed Weight_i x asset factor beta_i.", "Può essere negativo per SHORT o per beta condizionale negativo."],
        ["Qualità", "PxGap", "Yahoo current price / Fineco market price - 1.", "Controllo qualità dati; <1% OK, 1-3% warning, >3% investigate."],
        ["Limiti", "Current-weight backtest", "I pesi correnti sono applicati retrospettivamente alla storia.", "Non ricostruisce le reali posizioni storiche del portafoglio."],
        ["Limiti", "FX", "Il rischio valutario EUR/USD, EUR/GBP, EUR/JPY ecc. non è modellato esplicitamente.", "Può essere rilevante per strumenti non EUR."],
        ["Limiti", "Non linearità / leverage", "Opzioni, payoff non lineari, financing cost, gap estremi e liquidità non sono modellati esplicitamente.", "I risultati non rappresentano uno stress test completo né una misura regolamentare."],
    ]


def _legend_rows_en() -> list[list[str]]:
    return [
        ["SECTION", "ITEM", "DEFINITION / FORMULA / ASSUMPTION", "INTERPRETATION / LIMITS"],
        ["Data", "Price source", "Yahoo Finance via yfinance; adjusted historical prices when provided by the data vendor.", "Quality depends on the provider and on correct Fineco-to-Yahoo ticker resolution."],
        ["Data", "Technical window", "Maximum 252 sessions (~1 year).", "Used for 1Y performance and technical indicators; separate from the risk window."],
        ["Data", "Risk window", "Maximum 756 observations (~3 years); minimum 504 common observations (~2 years).", "Assets with insufficient history are excluded and Risk Coverage is reported."],
        ["Positions", "Dir", "LONG if quantity > 0; SHORT if quantity < 0; FLAT if quantity = 0.", "Direction is derived from quantity, not from the accounting sign of Fineco market value."],
        ["Positions", "Gross Exposure", "Sum of absolute position exposures.", "LONG and SHORT positions do not offset in gross exposure."],
        ["Positions", "Net Exposure", "Long Exposure - Short Exposure.", "Measures the portfolio's net directional exposure."],
        ["Positions", "Gross Weight", "|Exposure_i| / Gross Exposure.", "Always non-negative; used for concentration measures."],
        ["Positions", "Signed Weight / NetW", "Signed Exposure_i / Gross Exposure.", "LONG positive, SHORT negative; used by risk models."],
        ["Technical", "SMA20 / SMA50", "20- and 50-session Simple Moving Averages.", "Trend-structure indicators, not automatic trading signals."],
        ["Technical", "vsSMA20 / vsSMA50", "Current Price / SMA - 1.", "Percentage distance of price from the moving average."],
        ["Technical", "RSI14", "14-session Relative Strength Index using Wilder-style smoothing; scale 0-100.", ">70 traditionally strong/overbought; <30 weak/oversold; not an automatic signal."],
        ["Technical", "Vol20", "Std. dev. of daily returns over the latest 20 sessions x sqrt(252).", "Recent annualized historical volatility."],
        ["Technical", "RVOL", "Latest session volume / average volume of the previous 20 sessions.", ">1 means volume is above its recent average."],
        ["Technical", "Trend", "BULLISH: Price>SMA20>SMA50; BEARISH: Price<SMA20<SMA50; otherwise NEUTRAL.", "Deterministic classification of price structure."],
        ["Scoring", "Momentum Score", "0-100: Trend 30, vsSMA20 20, vsSMA50 20, RSI14 20, RVOL 10.", "Higher = stronger positive technical momentum under the project's heuristic rules."],
        ["Scoring", "Technical Risk Score", "0-100: Volatility 60, SMA20 extension 20, RSI extremes 20.", "Higher = greater technical risk; it is not VaR and does not by itself include correlation or position size."],
        ["Scoring", "Alignment", "LONG+BULLISH or SHORT+BEARISH = ALIGNED; opposite combination = AGAINST; neutral trend = NEUTRAL.", "Measures consistency between position direction and technical trend."],
        ["Concentration", "HHI", "Sum of squared gross weights.", "Higher = more concentration."],
        ["Concentration", "Effective Positions", "1 / HHI.", "Equivalent number of equally weighted positions."],
        ["Risk 2.1", "VolLoad", "Gross Weight x Vol20.", "Heuristic measure, not covariance-aware."],
        ["Risk 2.1", "TechLoad", "Gross Weight x Technical Risk Score.", "Exposure-weighted technical risk load."],
        ["Risk 2.2", "Portfolio Volatility", "sqrt(w' Sigma w), with signed w and annualized covariance matrix Sigma.", "Historical portfolio volatility relative to gross exposure."],
        ["Risk 2.2", "Component Risk", "w_i x marginal risk_i.", "May be negative: the position can act as a hedge."],
        ["Risk 2.2", "Risk Contribution", "Component Risk_i / Portfolio Volatility.", "Contributions sum to 100% subject to rounding."],
        ["Risk 2.2", "Diversification Ratio", "Weighted standalone volatility / Portfolio volatility.", ">1 indicates diversification benefit."],
        ["Risk 2.3", "Historical VaR", "Historical loss quantile at the selected confidence level; reported as a positive loss number.", "Not a maximum possible loss."],
        ["Risk 2.3", "CVaR / Expected Shortfall", "Average loss in observations at or beyond the VaR threshold.", "Describes tail severity beyond VaR."],
        ["Risk 2.3", "10D VaR/CVaR", "Actual rolling compounded 10-day portfolio returns.", "No square-root-of-time scaling is used."],
        ["Risk 2.3", "Tail N", "Number of empirical observations in the tail.", "<5 very low, 5-9 low, 10-19 moderate robustness."],
        ["Benchmark 2.4", "Beta", "Cov(Rp,Rm)/Var(Rm) versus the configured benchmark (default SPY).", "Historical benchmark sensitivity; a positive-beta SHORT reduces portfolio beta."],
        ["Benchmark 2.4", "Alpha", "Daily OLS intercept annualized linearly x252.", "Historical estimate, not a forecast of future excess return."],
        ["Benchmark 2.4", "R-squared", "Squared correlation in the single-factor model.", "Share of portfolio return variance explained by the benchmark."],
        ["Benchmark 2.4", "Systematic Vol", "|beta| x benchmark volatility.", "Volatility component associated with the benchmark in the one-factor model."],
        ["Benchmark 2.4", "Idiosyncratic Vol", "Annualized standard deviation of OLS residuals.", "Risk not explained by the benchmark."],
        ["Benchmark 2.4", "Beta Contribution", "Signed Weight_i x Beta_i.", "Contributions sum to Portfolio Beta on the same sample."],
        ["Multi-factor 2.5", "Model", "Rp = alpha + beta1 F1 + ... + betak Fk + epsilon.", "Coefficients are estimated simultaneously; each beta is conditional on the other factors."],
        ["Multi-factor 2.5", "Default factors", "US_MARKET=SPY, EUROPE=VGK, GOLD=GLD, TECH=QQQ.", "Configurable liquid-market proxies, not pure academic factors."],
        ["Multi-factor 2.5", "Adjusted R-squared", "R² adjusted for the number of model factors.", "Penalizes factors that do not add sufficient explanatory power."],
        ["Multi-factor 2.5", "VIF", "1/(1-R²_j), where factor j is regressed on the remaining factors.", ">5 elevated multicollinearity; >10 severe; individual betas may be unstable."],
        ["Multi-factor 2.5", "Condition Number", "Condition number of the standardized factor matrix.", "High values indicate near-collinearity."],
        ["Multi-factor 2.5", "Factor Beta Contribution", "Signed Weight_i x asset factor beta_i.", "Can be negative for SHORT positions or conditional negative betas."],
        ["Quality", "PxGap", "Yahoo current price / Fineco market price - 1.", "Data-quality check; <1% OK, 1-3% warning, >3% investigate."],
        ["Limitations", "Current-weight backtest", "Current portfolio weights are applied retrospectively to history.", "It does not reconstruct the portfolio's actual historical holdings."],
        ["Limitations", "FX", "EUR/USD, EUR/GBP, EUR/JPY and other currency risks are not modeled explicitly.", "Can be material for non-EUR instruments."],
        ["Limitations", "Nonlinearity / leverage", "Options, nonlinear payoffs, financing costs, extreme gaps and liquidity are not explicitly modeled.", "Results are not a complete stress test or regulatory risk measure."],
    ]


def _build_legend_sheet(workbook, name: str, title: str, subtitle: str, rows: list[list[str]]) -> None:
    ws = workbook.create_sheet(name)
    start = _title(ws, title, subtitle, width=4)
    headers = rows[0]
    data = rows[1:]
    _make_table(ws, start, headers, data, name.replace("_", "") + "Table")
    _format_columns(ws, {1: 20, 2: 28, 3: 74, 4: 70})
    for row in ws.iter_rows(min_row=start + 1):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = f"A{start + 1}"
    ws.sheet_view.showGridLines = False


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def export_portfolio_analysis_to_excel(
    file_path: str | Path,
    analyzed_positions: list[AnalyzedPosition],
    portfolio_risk: PortfolioRiskSummary,
    covariance_risk: PortfolioCovarianceRiskSummary,
    benchmark_risk: PortfolioBenchmarkRiskSummary,
    multifactor_risk: PortfolioMultiFactorSummary,
    tail_risk_1d: PortfolioHistoricalTailRiskSummary,
    tail_risk_10d: PortfolioHistoricalTailRiskSummary,
) -> Path:
    """
    Append/refresh Portfolio Analysis sheets in the original Fineco workbook.

    Existing Fineco sheets are preserved. Only sheets prefixed by PA_ and listed
    in REPORT_SHEETS are deleted/recreated on each run.
    """

    destination = Path(file_path)
    workbook = load_workbook(destination)

    _delete_previous_report_sheets(workbook)

    _build_overview(
        workbook,
        analyzed_positions,
        portfolio_risk,
        covariance_risk,
        benchmark_risk,
        multifactor_risk,
        tail_risk_1d,
        tail_risk_10d,
    )
    _build_positions(workbook, analyzed_positions, portfolio_risk)
    _build_covariance(workbook, analyzed_positions, covariance_risk)
    _build_benchmark(workbook, analyzed_positions, benchmark_risk)
    _build_multifactor(workbook, analyzed_positions, multifactor_risk)
    _build_tail_risk(workbook, tail_risk_1d, tail_risk_10d)
    _build_legend_sheet(
        workbook,
        "PA_Legenda_IT",
        "Assunzioni tecniche e legenda - Portfolio Analysis 2.5",
        "Documentazione in italiano delle formule, delle convenzioni LONG/SHORT, delle assunzioni e dei principali limiti del modello.",
        _legend_rows_it(),
    )
    _build_legend_sheet(
        workbook,
        "PA_Legend_EN",
        "Technical Assumptions and Legend - Portfolio Analysis 2.5",
        "English documentation of formulas, LONG/SHORT conventions, assumptions and principal model limitations.",
        _legend_rows_en(),
    )

    # Put the overview immediately after the original source sheet(s) by making
    # it active. We deliberately do not reorder Fineco's original worksheets.
    workbook.active = workbook.sheetnames.index("PA_Overview")

    _atomic_save(workbook, destination)
    return destination
