from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable
import pandas as pd

from app.ai.evidence_provider import (
    EvidenceFetchResult, EvidenceFetchStatus, EvidenceItem, EvidenceKind,
    EvidenceProvider, EvidenceProviderResponseError, EvidenceRequest, EvidenceSource,
)
from app.ai.research_service import ResearchEvidence
from app.market_data.yahoo_provider import get_price_history

HistoryLoader = Callable[[str], Any]

class YahooMarketEvidenceProvider(EvidenceProvider):
    """Real market evidence from the existing Yahoo market-data layer.

    Quant calculations remain deterministic Python. No LLM is called here.
    """
    def __init__(self, history_loader: HistoryLoader | None = None):
        self._history_loader = history_loader or get_price_history

    @property
    def provider_name(self) -> str:
        return "YAHOO_MARKET"

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        now = datetime.now(timezone.utc)
        try:
            raw = self._history_loader(request.ticker)
        except Exception as exc:
            raise EvidenceProviderResponseError(
                f"Yahoo history retrieval failed for {request.ticker}: {exc}"
            ) from exc

        df = self._coerce_frame(raw)
        if df.empty or "Close" not in df.columns:
            return EvidenceFetchResult(provider=self.provider_name, ticker=request.ticker,
                status=EvidenceFetchStatus.NO_DATA, items=[], warnings=["No usable price history"],
                fetched_at=now)

        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if close.empty:
            return EvidenceFetchResult(provider=self.provider_name, ticker=request.ticker,
                status=EvidenceFetchStatus.NO_DATA, items=[], warnings=["No usable close prices"],
                fetched_at=now)

        last = float(close.iloc[-1])
        facts=[f"Latest available close: {last:.4f}."]
        for sessions,label in [(1,"1-session"),(5,"5-session"),(20,"20-session")]:
            if len(close)>sessions:
                ret=(last/float(close.iloc[-1-sessions])-1.0)*100
                facts.append(f"{label} price return: {ret:.2f}%.")

        if len(close)>=20:
            sma20=float(close.tail(20).mean())
            facts.append(f"SMA20: {sma20:.4f}; close vs SMA20: {(last/sma20-1)*100:.2f}%.")
        if len(close)>=50:
            sma50=float(close.tail(50).mean())
            facts.append(f"SMA50: {sma50:.4f}; close vs SMA50: {(last/sma50-1)*100:.2f}%.")

        if len(close)>=15:
            delta=close.diff()
            gain=delta.clip(lower=0).tail(14).mean()
            loss=(-delta.clip(upper=0)).tail(14).mean()
            if loss == 0:
                rsi=100.0
            else:
                rsi=100-(100/(1+(gain/loss)))
            facts.append(f"RSI14 (simple rolling mean method): {float(rsi):.2f}.")

        if "Volume" in df.columns:
            volume=pd.to_numeric(df["Volume"],errors="coerce").dropna()
            if len(volume)>=20 and float(volume.tail(20).mean())>0:
                rvol=float(volume.iloc[-1])/float(volume.tail(20).mean())
                facts.append(f"Latest volume / 20-session average volume (RVOL): {rvol:.2f}x.")

        published=self._last_timestamp(df, now)
        text=" ".join(facts)
        digest=sha256(f"{request.ticker}|{published.isoformat()}|{text}".encode()).hexdigest()[:12]
        evidence_id=f"EVID-YAHOO-{request.ticker}-{digest}"
        source_id=f"SRC-YAHOO-{request.ticker}-{digest}"

        source=EvidenceSource(source_id=source_id, provider=self.provider_name,
            source_type="MARKET_DATA", source_name="Yahoo Finance market data",
            source_url=f"https://finance.yahoo.com/quote/{request.ticker}/history",
            retrieved_at=now, published_at=published,
            metadata={"ticker":request.ticker})
        evidence=ResearchEvidence(evidence_id=evidence_id, source_type="MARKET_DATA",
            text=text, published_at=published,
            metadata={"provider":self.provider_name,"source_id":source_id})
        item=EvidenceItem(evidence=evidence, source=source, kind=EvidenceKind.MARKET,
            ticker=request.ticker)
        return EvidenceFetchResult(provider=self.provider_name,ticker=request.ticker,
            status=EvidenceFetchStatus.SUCCESS,items=[item],fetched_at=now)

    @staticmethod
    def _coerce_frame(raw: Any) -> pd.DataFrame:
        if isinstance(raw,pd.DataFrame): return raw.copy()
        if hasattr(raw,"history") and isinstance(raw.history,pd.DataFrame): return raw.history.copy()
        raise EvidenceProviderResponseError("Unsupported Yahoo history result type")

    @staticmethod
    def _last_timestamp(df: pd.DataFrame, fallback: datetime) -> datetime:
        try:
            ts=pd.Timestamp(df.index[-1])
            if ts.tzinfo is None: ts=ts.tz_localize("UTC")
            else: ts=ts.tz_convert("UTC")
            return ts.to_pydatetime()
        except Exception:
            return fallback
