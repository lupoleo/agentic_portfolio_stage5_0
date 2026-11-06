import json

from tools import audit_portfolio_instrument_coverage as tool


def test_audit_reports_identity_and_method_counts(monkeypatch, tmp_path, capsys):
    from app.portfolio.models import PortfolioPosition

    def item(symbol, method, reference=None):
        return PortfolioPosition(
            "x", "I", symbol, "M", "T", "EUR", 1, 1, 1, 1, 1, 1,
            10, 0, 0, 0, market_data_symbol=symbol,
            market_data_method=method, instrument_reference_id=reference,
        )

    monkeypatch.setattr(tool, "load_fineco_positions", lambda _: [
        item("AAA", "DIRECT"),
        item("BBB", "LEVERAGED_PROXY", "ref-1"),
    ])
    monkeypatch.setattr(tool, "resolve_yahoo_symbol", lambda x: x.market_data_symbol)
    output = tmp_path / "audit.json"
    monkeypatch.setattr("sys.argv", ["audit", "--portfolio", "x.xlsx", "--output", str(output)])
    assert tool.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["position_count"] == 2
    assert payload["identity_represented_count"] == 2
    assert payload["leveraged_proxy_count"] == 1
    assert payload["network_calls"] == 0
