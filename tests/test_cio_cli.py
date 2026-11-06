from pathlib import Path
from types import SimpleNamespace

from app.cio.cli import build_parser, main, opportunity_size
from app.cio.models import Currency, OpportunityStatus
from app.cio.storage import Stage3Store


def _feed_inputs(monkeypatch, values):
    iterator = iter(values)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt="": next(iterator),
    )


def test_account_show_when_not_configured(tmp_path, capsys):
    db = tmp_path / "cio.db"

    main([
        "--db", str(db),
        "account", "show",
    ])

    out = capsys.readouterr().out
    assert "No Account State configured." in out


def test_account_set_then_show(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cio.db"

    _feed_inputs(
        monkeypatch,
        [
            "18500",  # EUR available
            "5000",   # EUR reserve
            "6200",   # USD available
            "1000",   # USD reserve
            "25000",  # Account equity / portfolio NAV EUR
            "500",    # max loss
            "15",     # max position
            "",       # max portfolio gross exposure
            "80",     # max CIO deployable
            "1.05",   # max beta
            "",       # max var
            "Initial operator state",
        ],
    )

    main([
        "--db", str(db),
        "account", "set",
    ])

    store = Stage3Store(db)
    state = store.get_latest_account_state()

    assert state is not None

    eur = next(
        x
        for x in state.cash
        if x.currency is Currency.EUR
    )

    usd = next(
        x
        for x in state.cash
        if x.currency is Currency.USD
    )

    assert eur.available == 18500
    assert eur.reserve == 5000

    assert usd.available == 6200
    assert usd.reserve == 1000

    assert state.account_equity_eur == 25000

    assert state.constraints.max_trade_loss_eur == 500
    assert state.constraints.max_position_weight_pct == 15

    assert (
        state.constraints.max_portfolio_gross_exposure_pct
        is None
    )

    assert (
        state.constraints.max_cio_deployable_pct
        == 80
    )

    assert state.constraints.max_portfolio_beta == 1.05

    main([
        "--db", str(db),
        "account", "show",
    ])

    out = capsys.readouterr().out

    assert "CIO ACCOUNT STATE" in out
    assert "€18,500.00" in out
    assert "$6,200.00" in out
    assert "NAV/Equity" in out
    assert "€25,000.00" in out
    assert "Max portfolio gross exposure %" in out
    assert "Max CIO deployable %" in out
    assert "80.0" in out
    assert "Initial operator state" in out

def test_instrument_show_when_cache_missing(tmp_path, capsys):
    db = tmp_path / "cio.db"

    main([
        "--db", str(db),
        "instruments", "show", "GOOGL",
    ])

    out = capsys.readouterr().out
    assert (
        "No Fineco instruments known for GOOGL."
        in out
    )


def test_instrument_add_show_and_list(
    tmp_path,
    monkeypatch,
    capsys,
):
    db = tmp_path / "cio.db"

    _feed_inputs(
        monkeypatch,
        [
            "MSFT",
            "Microsoft CFDC",
            "CFDC",
            "INTRADAY",
            "MSFTCFD",
            "CFDC",
            "USD",
            "EUR",
            "EUR",
            "y",   # LONG
            "y",   # SHORT
            "y",   # intraday
            "y",   # overnight
            "5",   # leverage
            "20",  # margin
            "0.01",# tick size
            "USD", # tick currency
            "",    # commission
            "",    # overnight financing
            "Confirmed from Fineco screen",
        ],
    )

    main([
        "--db", str(db),
        "instruments", "add",
    ])

    store = Stage3Store(db)
    instruments = store.find_fineco_instruments(
        "msft"
    )

    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.underlying == "MSFT"
    assert instrument.leverage == 5
    assert instrument.margin_pct == 20
    assert instrument.short_available is True

    main([
        "--db", str(db),
        "instruments", "show", "MSFT",
    ])

    show_out = capsys.readouterr().out
    assert "Microsoft CFDC" in show_out
    assert "5x" in show_out
    assert "20%" in show_out

    main([
        "--db", str(db),
        "instruments", "list",
    ])

    list_out = capsys.readouterr().out
    assert "FINECO INSTRUMENT CACHE" in list_out
    assert "MSFT" in list_out


# =============================================================
# Opportunity size instrument override
# =============================================================


def test_size_parser_accepts_instrument_override():
    parser = build_parser()

    args = parser.parse_args([
        "opportunities",
        "size",
        "OPP-HOOD-TEST",
        "--instrument-id",
        "FIN-HOOD-NASDAQ",
    ])

    assert args.resource == "opportunities"
    assert args.action == "size"
    assert args.opportunity_id == "OPP-HOOD-TEST"
    assert args.instrument_id == "FIN-HOOD-NASDAQ"


def test_size_parser_defaults_instrument_override_to_none():
    parser = build_parser()

    args = parser.parse_args([
        "opportunities",
        "size",
        "OPP-HOOD-TEST",
    ])

    assert args.instrument_id is None


def test_opportunity_size_rejects_unknown_override(capsys):
    opportunity = SimpleNamespace(
        opportunity_id="OPP-HOOD-TEST",
        ticker="HOOD",
        direction=SimpleNamespace(value="LONG"),
        status=OpportunityStatus.INSTRUMENTS_RANKED,
    )

    class FakeStore:
        def get_trade_opportunity(self, opportunity_id):
            assert opportunity_id == "OPP-HOOD-TEST"
            return opportunity

        def list_instrument_candidates(self, opportunity_id):
            assert opportunity_id == "OPP-HOOD-TEST"
            return []

    opportunity_size(
        FakeStore(),
        "OPP-HOOD-TEST",
        instrument_id="FIN-NOT-FOUND",
    )

    out = capsys.readouterr().out
    assert (
        "No persisted instrument candidate found: "
        "FIN-NOT-FOUND"
        in out
    )


def test_opportunity_size_rejects_ineligible_override(capsys):
    opportunity = SimpleNamespace(
        opportunity_id="OPP-HOOD-TEST",
        ticker="HOOD",
        direction=SimpleNamespace(value="LONG"),
        status=OpportunityStatus.INSTRUMENTS_RANKED,
    )

    candidate = SimpleNamespace(
        instrument_id="FIN-HOOD-INTRADAY",
        eligible=False,
        rejection_reason=(
            "Intraday-only instrument cannot implement "
            "a multi-session opportunity."
        ),
    )

    class FakeStore:
        def get_trade_opportunity(self, opportunity_id):
            return opportunity

        def list_instrument_candidates(self, opportunity_id):
            return [candidate]

    opportunity_size(
        FakeStore(),
        "OPP-HOOD-TEST",
        instrument_id="FIN-HOOD-INTRADAY",
    )

    out = capsys.readouterr().out
    assert "Instrument override rejected." in out
    assert "FIN-HOOD-INTRADAY" in out
    assert "candidate is not eligible" in out
    assert "Intraday-only instrument" in out


def test_opportunity_size_uses_operator_override(
    monkeypatch,
    capsys,
):
    opportunity = SimpleNamespace(
        opportunity_id="OPP-HOOD-TEST",
        ticker="HOOD",
        direction=SimpleNamespace(value="LONG"),
        status=OpportunityStatus.INSTRUMENTS_RANKED,
    )

    candidate = SimpleNamespace(
        instrument_id="FIN-HOOD-NASDAQ",
        eligible=True,
        rejection_reason=None,
        suitability_score=0.85,
    )

    instrument = SimpleNamespace(
        instrument_id="FIN-HOOD-NASDAQ",
        description="Robinhood Markets RG-A Ordinary NASDAQ",
        market_snapshot=None,
        quote_currency=Currency.USD,
    )

    account_state = SimpleNamespace()

    sizing = SimpleNamespace(
        sizing_id="SIZE-TEST",
        execution_side=SimpleNamespace(value="BUY"),
        quantity=30.0,
        reference_price=107.135,
        currency=Currency.USD,
        stop_price=None,
        gross_exposure_eur=2800.0,
        estimated_capital_required_eur=2800.0,
        estimated_margin_eur=None,
        estimated_max_loss_eur=None,
        risk_budget_eur=300.0,
        constraints_passed=True,
        violated_constraints=[],
    )

    class FakeSizer:
        def size(self, **kwargs):
            assert (
                kwargs["instrument"].instrument_id
                == "FIN-HOOD-NASDAQ"
            )
            assert kwargs["reference_price"] == 107.135
            assert kwargs["fx_to_eur"] == 0.86
            return sizing

    class FakeStore:
        def __init__(self):
            self.saved_sizing = None

        def get_trade_opportunity(self, opportunity_id):
            return opportunity

        def list_instrument_candidates(self, opportunity_id):
            return [candidate]

        def get_fineco_instrument(self, instrument_id):
            assert instrument_id == "FIN-HOOD-NASDAQ"
            return instrument

        def get_latest_account_state(self):
            return account_state

        def save_position_sizing(self, value):
            self.saved_sizing = value

        def mark_trade_opportunity_position_sized(
            self,
            opportunity_id,
            sizing_id,
        ):
            assert opportunity_id == "OPP-HOOD-TEST"
            assert sizing_id == "SIZE-TEST"
            return SimpleNamespace(
                status=OpportunityStatus.POSITION_SIZED
            )

    store = FakeStore()

    monkeypatch.setattr(
        "app.cio.cli.PositionSizer",
        lambda: FakeSizer(),
    )

    _feed_inputs(
        monkeypatch,
        [
            "107.135",  # Reference price USD
            "0.86",     # 1 USD = EUR
            "",         # Stop
            "",         # Exposure override
        ],
    )

    opportunity_size(
        store,
        "OPP-HOOD-TEST",
        instrument_id="FIN-HOOD-NASDAQ",
    )

    out = capsys.readouterr().out

    assert store.saved_sizing is sizing
    assert (
        "Instrument:   "
        "Robinhood Markets RG-A Ordinary NASDAQ"
        in out
    )
    assert (
        "Instrument ID: "
        "FIN-HOOD-NASDAQ"
        in out
    )
    assert "Selector score: 0.85" in out
    assert (
        "Selection mode: OPERATOR_OVERRIDE"
        in out
    )
    assert "Opportunity status: POSITION_SIZED" in out


def test_opportunity_size_keeps_legacy_top_ranked_behavior(
    monkeypatch,
    capsys,
):
    opportunity = SimpleNamespace(
        opportunity_id="OPP-HOOD-TEST",
        ticker="HOOD",
        direction=SimpleNamespace(value="LONG"),
        status=OpportunityStatus.INSTRUMENTS_RANKED,
    )

    candidate = SimpleNamespace(
        instrument_id="FIN-HOOD-MILANO",
        eligible=True,
        rejection_reason=None,
        suitability_score=0.85,
    )

    instrument = SimpleNamespace(
        instrument_id="FIN-HOOD-MILANO",
        description="Robinhood Markets RG-A Ordinary Milano",
        market_snapshot=None,
        quote_currency=Currency.EUR,
    )

    account_state = SimpleNamespace()

    sizing = SimpleNamespace(
        sizing_id="SIZE-TEST-AUTO",
        execution_side=SimpleNamespace(value="BUY"),
        quantity=30.0,
        reference_price=92.0,
        currency=Currency.EUR,
        stop_price=None,
        gross_exposure_eur=2760.0,
        estimated_capital_required_eur=2760.0,
        estimated_margin_eur=None,
        estimated_max_loss_eur=None,
        risk_budget_eur=300.0,
        constraints_passed=True,
        violated_constraints=[],
    )

    class FakeSizer:
        def size(self, **kwargs):
            assert (
                kwargs["instrument"].instrument_id
                == "FIN-HOOD-MILANO"
            )
            assert kwargs["fx_to_eur"] == 1.0
            return sizing

    class FakeStore:
        def get_trade_opportunity(self, opportunity_id):
            return opportunity

        def get_top_instrument_candidate(self, opportunity_id):
            return candidate

        def get_fineco_instrument(self, instrument_id):
            return instrument

        def get_latest_account_state(self):
            return account_state

        def save_position_sizing(self, value):
            pass

        def mark_trade_opportunity_position_sized(
            self,
            opportunity_id,
            sizing_id,
        ):
            return SimpleNamespace(
                status=OpportunityStatus.POSITION_SIZED
            )

    monkeypatch.setattr(
        "app.cio.cli.PositionSizer",
        lambda: FakeSizer(),
    )

    _feed_inputs(
        monkeypatch,
        [
            "92.0",  # Reference price EUR
            "",      # Stop
            "",      # Exposure override
        ],
    )

    opportunity_size(
        FakeStore(),
        "OPP-HOOD-TEST",
    )

    out = capsys.readouterr().out
    assert "Selection mode: AUTO_TOP_RANKED" in out
    assert "FX to EUR: 1.0 (EUR instrument)" in out