from datetime import datetime, timezone

from app.cio.models import (
    AccountState,
    CacheStatus,
    Currency,
    CurrencyCash,
    DataSource,
    FinecoInstrument,
    InstrumentType,
    PortfolioSnapshot,
)
from app.cio.storage import Stage3Store


NOW = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)


def test_store_round_trip_account_state(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")

    state = AccountState(
        account_state_id="ACC-1",
        timestamp=NOW,
        cash=[
            CurrencyCash(
                currency=Currency.EUR,
                available=10_000,
                reserve=2_000,
            )
        ],
        source=DataSource.OPERATOR,
    )

    store.save_account_state(state)

    loaded = store.get_account_state("ACC-1")
    latest = store.get_latest_account_state()

    assert loaded == state
    assert latest == state


def test_store_round_trip_snapshot_and_file_hash_lookup(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")

    state = AccountState(
        account_state_id="ACC-1",
        timestamp=NOW,
        cash=[
            CurrencyCash(
                currency=Currency.EUR,
                available=10_000,
            )
        ],
        source=DataSource.OPERATOR,
    )
    store.save_account_state(state)

    snapshot = PortfolioSnapshot(
        snapshot_id="SNAP-1",
        timestamp=NOW,
        source_file="data/input/portafoglio-export.xlsx",
        source_file_hash="sha256-test",
        quant_engine_version="2.5.0",
        analyzed_positions=31,
        gross_exposure_eur=326_774.28,
        net_exposure_eur=326_774.28,
        account_state_id="ACC-1",
    )

    store.save_portfolio_snapshot(snapshot)

    assert store.get_portfolio_snapshot("SNAP-1") == snapshot
    assert store.get_latest_portfolio_snapshot() == snapshot
    assert store.find_snapshot_by_file_hash("sha256-test") == snapshot


def test_fineco_instrument_cache_lookup_is_case_insensitive(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")

    ordinary = FinecoInstrument(
        instrument_id="FIN-GOOGL-ORD",
        underlying="GOOGL",
        description="Alphabet A",
        instrument_type=InstrumentType.ORDINARY,
        currency=Currency.USD,
        long_available=True,
        short_available=True,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
    )

    margin = FinecoInstrument(
        instrument_id="FIN-GOOGL-MARGIN-X10",
        underlying="GOOGL",
        description="Alphabet margin overnight",
        instrument_type=InstrumentType.MARGIN,
        currency=Currency.USD,
        long_available=True,
        short_available=True,
        overnight_available=True,
        leverage=10,
        margin_pct=10,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
    )

    store.save_fineco_instrument(ordinary)
    store.save_fineco_instrument(margin)

    results = store.find_fineco_instruments("googl")

    assert {item.instrument_id for item in results} == {
        "FIN-GOOGL-ORD",
        "FIN-GOOGL-MARGIN-X10",
    }


def test_saving_same_instrument_updates_cache_entry(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")

    original = FinecoInstrument(
        instrument_id="FIN-MSFT-CFDC-X5",
        underlying="MSFT",
        instrument_type=InstrumentType.CFDC,
        currency=Currency.USD,
        leverage=5,
        margin_pct=None,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.UNKNOWN,
    )

    updated = original.model_copy(
        update={
            "margin_pct": 20,
            "cache_status": CacheStatus.CURRENT,
        }
    )

    store.save_fineco_instrument(original)
    store.save_fineco_instrument(updated)

    loaded = store.get_fineco_instrument("FIN-MSFT-CFDC-X5")

    assert loaded is not None
    assert loaded.margin_pct == 20
    assert loaded.cache_status is CacheStatus.CURRENT
