from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from app.ai.canonical_technical import CanonicalTechnicalInput
from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceRequest,
    EvidenceSource,
)
from app.ai.research_service import ResearchEvidence


# v2 (AI-8C.3-R1) adds the Stage-2 20-session annualized volatility.
TECHNICAL_EVIDENCE_ADAPTER_VERSION = "stage4-research-technical-v2"


class CanonicalTechnicalEvidenceAdapter:
    """Convert the frozen Stage-2 technical input into research evidence."""

    provider_name = "CANONICAL_TECHNICAL"

    def adapt(
        self,
        request: EvidenceRequest,
        value: CanonicalTechnicalInput | None,
        *,
        fetched_at: datetime | None = None,
    ) -> EvidenceFetchResult:
        timestamp = self._clock(request, fetched_at)
        if value is None:
            return EvidenceFetchResult(
                provider=self.provider_name,
                ticker=request.ticker,
                status=EvidenceFetchStatus.NO_DATA,
                items=[],
                warnings=["Canonical technical input is unavailable"],
                fetched_at=timestamp,
            )
        if value.ticker.strip().upper() != request.ticker:
            raise ValueError("canonical technical ticker differs from request")

        facts = [
            f"Latest price: {value.current_price:.6f}.",
            self._percent("1-session return", value.return_1d_pct),
            self._percent("5-session return", value.return_5d_pct),
            self._percent("20-session return", value.return_20d_pct),
            f"SMA20: {value.sma20:.6f}.",
            f"SMA50: {value.sma50:.6f}.",
            f"Close versus SMA20: {value.close_vs_sma20_pct:.4f}%.",
            f"Close versus SMA50: {value.close_vs_sma50_pct:.4f}%.",
            f"RSI14: {value.rsi14:.4f}.",
            f"RVOL: {value.rvol:.4f}x.",
            self._percent(
                "20-session annualized volatility",
                value.volatility_20d_pct,
            ),
            f"Canonical trend: {value.trend}.",
        ]
        text = " ".join(part for part in facts if part is not None)
        digest = sha256(
            f"{request.ticker}|{timestamp.isoformat()}|{text}".encode("utf-8")
        ).hexdigest()[:16]
        evidence_id = f"EVID-TECH-{request.ticker}-{digest}"
        source_id = f"SRC-TECH-{request.ticker}-{digest}"
        metadata: dict[str, Any] = {
            "ticker": request.ticker,
            "canonical_source": value.source,
            "canonical_technical_contract": value.contract_version,
            "immutable_as_of": timestamp.isoformat(),
        }
        source = EvidenceSource(
            source_id=source_id,
            provider=self.provider_name,
            source_type="TECHNICAL",
            source_name="Stage-2 canonical technical analysis",
            retrieved_at=timestamp,
            published_at=timestamp,
            metadata=metadata,
        )
        evidence = ResearchEvidence(
            evidence_id=evidence_id,
            source_type="TECHNICAL",
            text=text,
            published_at=timestamp,
            metadata={
                "provider": self.provider_name,
                "source_id": source_id,
                **metadata,
            },
        )
        item = EvidenceItem(
            evidence=evidence,
            source=source,
            kind=EvidenceKind.TECHNICAL,
            ticker=request.ticker,
            metadata={"adapter_version": TECHNICAL_EVIDENCE_ADAPTER_VERSION},
        )
        return EvidenceFetchResult(
            provider=self.provider_name,
            ticker=request.ticker,
            status=EvidenceFetchStatus.SUCCESS,
            items=[item],
            fetched_at=timestamp,
            metadata={"adapter_version": TECHNICAL_EVIDENCE_ADAPTER_VERSION},
        )

    @staticmethod
    def _clock(
        request: EvidenceRequest,
        fetched_at: datetime | None,
    ) -> datetime:
        value = fetched_at or request.as_of or datetime.now(timezone.utc)
        if value.tzinfo is None:
            raise ValueError("technical evidence clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _percent(label: str, value: float | None) -> str | None:
        if value is None:
            return None
        return f"{label}: {value:.4f}%."
