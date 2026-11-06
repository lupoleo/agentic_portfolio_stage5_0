"""Ticker → IBKR instrument identity for one run (architecture §5D.3).

The 4.0 research pipeline passes only a ticker to evidence providers. The
directory supplies the IBKR identity (conid, ISIN, held exchange, asset kind,
other listings) that the IBKR providers need, built from the account state of
the run (live positions or the TEMPORARY simulation) and, later, screener rows.
Unknown tickers stay unknown: providers then return NO_DATA instead of
searching by ticker.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from .models import AccountReading, BrokerModel
from .sources import SimulationFixture


class InstrumentRef(BrokerModel):
    ticker: str = Field(min_length=1)
    conid: int = Field(gt=0)
    isin: str | None = None
    exchange: str = "SMART"
    asset_kind: str = "STK"  # STK, ETF, ETC, ...
    currency: str | None = None
    alternatives: tuple[int, ...] = ()

    def as_metadata(self) -> dict:
        return {
            "conid": self.conid,
            "isin": self.isin,
            "exchange": self.exchange,
            "asset_kind": self.asset_kind,
            "currency": self.currency,
            "alternatives": list(self.alternatives),
        }


class InstrumentDirectory:
    def __init__(self, refs: Iterable[InstrumentRef] = ()) -> None:
        self._by_ticker: dict[str, InstrumentRef] = {}
        for ref in refs:
            self.add(ref)

    def add(self, ref: InstrumentRef) -> None:
        key = ref.ticker.strip().upper()
        existing = self._by_ticker.get(key)
        if existing is not None and existing.conid != ref.conid:
            raise ValueError(f"ticker {key} maps to two instruments: {existing.conid} and {ref.conid}")
        self._by_ticker[key] = ref

    def get(self, ticker: str) -> InstrumentRef | None:
        return self._by_ticker.get(ticker.strip().upper())

    def __len__(self) -> int:
        return len(self._by_ticker)

    @classmethod
    def from_reading(cls, reading: AccountReading) -> "InstrumentDirectory":
        return cls(
            InstrumentRef(ticker=p.symbol, conid=p.conid, isin=p.isin, asset_kind=p.asset_class, currency=p.currency)
            for p in reading.positions
        )

    @classmethod
    def from_fixture(cls, fixture: SimulationFixture) -> "InstrumentDirectory":
        return cls(
            InstrumentRef(ticker=h.symbol, conid=h.conid, isin=h.isin, currency=h.currency) for h in fixture.holdings
        )
