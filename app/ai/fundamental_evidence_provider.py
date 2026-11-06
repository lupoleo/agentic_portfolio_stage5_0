from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import math
from typing import Any, Callable

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceProvider,
    EvidenceProviderResponseError,
    EvidenceRequest,
    EvidenceSource,
)
from app.ai.research_service import ResearchEvidence


SnapshotLoader = Callable[[str], Any]
FUNDAMENTAL_EVIDENCE_POLICY_VERSION = "yahoo-fundamental-evidence-v3-financials-aware"

# AI-8C.2-R2: industrial metrics that are not meaningful for banks and other
# deposit-funded financial companies (gross margin is reported as 0 and
# operating cash flow swings with deposits and loans).
_FINANCIAL_SECTOR_OMITTED_FIELDS = frozenset({
    "grossMargins", "operatingCashflow", "freeCashflow",
    "currentRatio", "quickRatio", "enterpriseToEbitda",
})
_FINANCIAL_SECTOR_NOTE = (
    "Financial-sector company: gross margin, operating and free cash flow, "
    "liquidity ratios and EV/EBITDA are not meaningful for banks and are "
    "omitted; total debt includes funding liabilities."
)

_MONETARY_FIELDS = frozenset({
    "totalRevenue",
    "operatingCashflow",
    "freeCashflow",
    "totalCash",
    "totalDebt",
})
_PERCENT_FIELDS = frozenset({
    "revenueGrowth",
    "earningsGrowth",
    "grossMargins",
    "operatingMargins",
    "profitMargins",
})

_FIELDS = (
    ("totalRevenue", "Revenue"),
    ("revenueGrowth", "Revenue growth"),
    ("trailingEps", "Trailing EPS"),
    ("forwardEps", "Forward EPS estimate"),
    ("earningsGrowth", "Earnings growth"),
    ("grossMargins", "Gross margin"),
    ("operatingMargins", "Operating margin"),
    ("profitMargins", "Profit margin"),
    ("operatingCashflow", "Operating cash flow"),
    ("freeCashflow", "Free cash flow"),
    ("totalCash", "Total cash"),
    ("totalDebt", "Total debt"),
    ("currentRatio", "Current liquidity ratio"),
    ("quickRatio", "Quick liquidity ratio"),
    ("trailingPE", "Trailing P/E valuation multiple"),
    ("forwardPE", "Forward P/E valuation multiple"),
    ("priceToBook", "Price-to-book valuation multiple"),
    ("enterpriseToEbitda", "Enterprise-value-to-EBITDA valuation multiple"),
)


class YahooFundamentalEvidenceProvider(EvidenceProvider):
    """Normalize a bounded Yahoo company snapshot into fundamental evidence."""

    def __init__(self, snapshot_loader: SnapshotLoader | None = None) -> None:
        self._snapshot_loader = snapshot_loader or self._default_snapshot_loader

    @property
    def provider_name(self) -> str:
        return "YAHOO_FUNDAMENTAL"

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        fetched_at = self._clock(request)
        try:
            raw = self._snapshot_loader(request.ticker)
        except Exception as exc:
            raise EvidenceProviderResponseError(
                f"Yahoo fundamental retrieval failed for {request.ticker}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise EvidenceProviderResponseError(
                "Unsupported Yahoo fundamental result type"
            )

        currency = self._scalar(
            raw.get("financialCurrency") or raw.get("currency")
        )
        currency = currency if isinstance(currency, str) else None
        facts: list[str] = []
        selected: dict[str, Any] = {}
        financial_sector = self._is_financial_sector(raw)
        for key, label in _FIELDS:
            if financial_sector and key in _FINANCIAL_SECTOR_OMITTED_FIELDS:
                continue
            value = self._scalar(raw.get(key))
            if value is None:
                continue
            selected[key] = value
            facts.append(
                f"{label}: {self._format_field(key, value, currency)}."
            )
        if not facts:
            return EvidenceFetchResult(
                provider=self.provider_name,
                ticker=request.ticker,
                status=EvidenceFetchStatus.NO_DATA,
                items=[],
                warnings=["No usable Yahoo fundamental fields returned"],
                fetched_at=fetched_at,
            )

        quarter = self._timestamp(raw.get("mostRecentQuarter"))
        if quarter is not None and request.as_of is not None:
            as_of = self._utc(request.as_of)
            if quarter > as_of:
                quarter = None
        if financial_sector:
            facts.append(_FINANCIAL_SECTOR_NOTE)
        text = "Fundamental company snapshot. " + " ".join(facts)
        digest = sha256(
            (
                f"{request.ticker}|{quarter}|{selected}|{currency}|"
                f"{FUNDAMENTAL_EVIDENCE_POLICY_VERSION}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        evidence_id = f"EVID-YFUND-{request.ticker}-{digest}"
        source_id = f"SRC-YFUND-{request.ticker}-{digest}"
        metadata = {
            "ticker": request.ticker,
            "field_names": sorted(selected),
            "field_count": len(selected),
            "financial_currency": currency,
            "immutable_as_of": fetched_at.isoformat(),
            "policy_version": FUNDAMENTAL_EVIDENCE_POLICY_VERSION,
            "financial_sector": financial_sector,
        }
        source = EvidenceSource(
            source_id=source_id,
            provider=self.provider_name,
            source_type="FUNDAMENTAL",
            source_name="Yahoo Finance company fundamentals",
            source_url=f"https://finance.yahoo.com/quote/{request.ticker}/financials",
            retrieved_at=fetched_at,
            published_at=quarter,
            metadata=metadata,
        )
        item = EvidenceItem(
            evidence=ResearchEvidence(
                evidence_id=evidence_id,
                source_type="FUNDAMENTAL",
                text=text,
                published_at=quarter,
                metadata={
                    "provider": self.provider_name,
                    "source_id": source_id,
                    **metadata,
                },
            ),
            source=source,
            kind=EvidenceKind.FUNDAMENTAL,
            ticker=request.ticker,
        )
        return EvidenceFetchResult(
            provider=self.provider_name,
            ticker=request.ticker,
            status=EvidenceFetchStatus.SUCCESS,
            items=[item],
            fetched_at=fetched_at,
            metadata={"field_count": len(selected)},
        )

    @staticmethod
    def _is_financial_sector(raw: dict[str, Any]) -> bool:
        sector = str(raw.get("sector") or "").strip().lower()
        industry = str(raw.get("industry") or "").strip().lower()
        return sector == "financial services" and (
            "bank" in industry or "insurance" in industry or not industry
        )

    @staticmethod
    def _default_snapshot_loader(ticker: str) -> dict[str, Any]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise EvidenceProviderResponseError(
                "yfinance is required for YahooFundamentalEvidenceProvider"
            ) from exc
        return dict(yf.Ticker(ticker).info or {})

    @staticmethod
    def _clock(request: EvidenceRequest) -> datetime:
        return YahooFundamentalEvidenceProvider._utc(
            request.as_of or datetime.now(timezone.utc)
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("evidence clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return YahooFundamentalEvidenceProvider._utc(value)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return None
        return None

    @staticmethod
    def _scalar(value: Any) -> str | int | float | bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _format(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)

    @classmethod
    def _format_field(
        cls,
        key: str,
        value: Any,
        currency: str | None,
    ) -> str:
        if (
            key in _MONETARY_FIELDS
            and not isinstance(value, bool)
            and isinstance(value, (int, float))
        ):
            number = float(value)
            magnitude = abs(number)
            if magnitude >= 1_000_000_000:
                scaled = f"{number / 1_000_000_000:.6g} billion"
            elif magnitude >= 1_000_000:
                scaled = f"{number / 1_000_000:.6g} million"
            elif magnitude >= 1_000:
                scaled = f"{number / 1_000:.6g} thousand"
            else:
                scaled = f"{number:.6g}"
            exact = (
                f"{value:,d}" if isinstance(value, int)
                else f"{number:,.6f}".rstrip("0").rstrip(".")
            )
            prefix = f"{currency} " if currency else ""
            return f"{prefix}{scaled} (exact raw units: {exact})"
        if (
            key in _PERCENT_FIELDS
            and not isinstance(value, bool)
            and isinstance(value, (int, float))
        ):
            number = float(value)
            return f"{number * 100.0:.6g}% (decimal: {number:.6g})"
        return cls._format(value)
