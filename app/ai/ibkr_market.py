"""IBKR daily history for the 4.0 market and canonical-technical calculations.

The 4.0 quant code works on a Yahoo-shaped ``DataFrame`` (Open, High, Low,
Close, Volume; DatetimeIndex). ``IbkrHistoryLoader`` builds that frame from IB
Gateway daily bars of the instrument in the run's ``InstrumentDirectory``, so
``YahooMarketEvidenceProvider`` and ``canonical_technical_from_history`` run
unchanged on IBKR data. Note: IBKR TRADES bars are split-adjusted but not
dividend-adjusted (Yahoo ``auto_adjust=True`` is both); return figures on
high-dividend names can differ slightly from 4.0.
"""

from __future__ import annotations

import pandas as pd

from app.ai.canonical_technical import canonical_technical_from_history
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.broker.gateway import GatewaySession, history_with_listing_fallback
from app.broker.instrument_directory import InstrumentDirectory
from app.broker.ibkr_client import BrokerNoDataError

PROVIDER_NAME = "IBKR_MARKET"


class IbkrHistoryLoader:
    def __init__(self, session: GatewaySession, directory: InstrumentDirectory, *, duration: str = "1 Y") -> None:
        self._session = session
        self._directory = directory
        self._duration = duration
        self._cache: dict[str, pd.DataFrame] = {}
        self.notes: dict[str, tuple[str, ...]] = {}

    def __call__(self, ticker: str, *_, **__) -> pd.DataFrame:
        key = ticker.strip().upper()
        if key in self._cache:
            return self._cache[key]
        ref = self._directory.get(key)
        if ref is None:
            raise BrokerNoDataError(f"{key} is not in the run's instrument directory")
        result = history_with_listing_fallback(
            self._session, ref.conid, ref.exchange, ref.alternatives, duration=self._duration
        )
        self.notes[key] = result.notes
        frame = pd.DataFrame(
            {
                "Open": [b.open for b in result.bars],
                "High": [b.high for b in result.bars],
                "Low": [b.low for b in result.bars],
                "Close": [b.close for b in result.bars],
                "Volume": [b.volume for b in result.bars],
            },
            index=pd.DatetimeIndex([pd.Timestamp(b.day) for b in result.bars], name="Date"),
        )
        self._cache[key] = frame
        return frame


class IbkrMarketEvidenceProvider(YahooMarketEvidenceProvider):
    """4.0 market facts computed on IBKR history, labelled as IBKR."""

    def __init__(self, loader: IbkrHistoryLoader) -> None:
        super().__init__(history_loader=loader)

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

    def fetch(self, request):
        result = super().fetch(request)
        items = []
        for item in result.items:
            source = item.source.model_copy(update={
                "source_id": item.source.source_id.replace("SRC-YAHOO", "SRC-IBKR"),
                "source_name": "IBKR market data (IB Gateway daily bars)",
                "source_url": None,
                "metadata": {**item.source.metadata, "source": "IBKR"},
            })
            evidence = item.evidence.model_copy(update={
                "evidence_id": item.evidence.evidence_id.replace("EVID-YAHOO", "EVID-IBKR"),
                "metadata": {**item.evidence.metadata, "provider": PROVIDER_NAME, "source_id": source.source_id},
            })
            items.append(item.model_copy(update={"source": source, "evidence": evidence, "metadata": {**item.metadata, "source": "IBKR"}}))
        return result.model_copy(update={"items": items, "metadata": {**result.metadata, "source": "IBKR"}})


def ibkr_canonical_technical_loader(loader: IbkrHistoryLoader):
    def load(ticker: str):
        return canonical_technical_from_history(ticker.strip().upper(), loader(ticker))

    return load
