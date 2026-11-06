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
ANALYST_EVIDENCE_POLICY_VERSION = "yahoo-analyst-evidence-v2-scorable"

_PRICE_TARGET_FIELDS = (
    "targetLowPrice",
    "targetMeanPrice",
    "targetMedianPrice",
    "targetHighPrice",
)

_FIELDS = (
    ("recommendationKey", "Analyst consensus recommendation"),
    ("recommendationMean", "Analyst consensus score"),
    ("numberOfAnalystOpinions", "Analyst opinion count"),
    ("targetLowPrice", "Analyst price target low"),
    ("targetMeanPrice", "Analyst price target mean"),
    ("targetMedianPrice", "Analyst price target median"),
    ("targetHighPrice", "Analyst price target high"),
    ("currentPrice", "Current price used by analyst snapshot"),
    ("forwardEps", "Forward EPS estimate"),
    ("trailingEps", "Trailing EPS for estimate comparison"),
    ("earningsQuarterlyGrowth", "Quarterly earnings growth estimate context"),
)


class YahooAnalystEvidenceProvider(EvidenceProvider):
    """Normalize Yahoo analyst consensus and expectations evidence."""

    def __init__(self, snapshot_loader: SnapshotLoader | None = None) -> None:
        self._snapshot_loader = snapshot_loader or self._default_snapshot_loader

    @property
    def provider_name(self) -> str:
        return "YAHOO_ANALYST"

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        fetched_at = self._clock(request)
        try:
            raw = self._snapshot_loader(request.ticker)
        except Exception as exc:
            raise EvidenceProviderResponseError(
                f"Yahoo analyst retrieval failed for {request.ticker}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise EvidenceProviderResponseError(
                "Unsupported Yahoo analyst result type"
            )

        merged = dict(raw.get("info") or {})
        targets = raw.get("analyst_price_targets")
        if isinstance(targets, dict):
            aliases = {
                "low": "targetLowPrice",
                "mean": "targetMeanPrice",
                "median": "targetMedianPrice",
                "high": "targetHighPrice",
                "current": "currentPrice",
            }
            for source, target in aliases.items():
                if source in targets and target not in merged:
                    merged[target] = targets[source]

        facts: list[str] = []
        selected: dict[str, Any] = {}
        for key, label in _FIELDS:
            value = self._scalar(merged.get(key))
            if value is None:
                continue
            selected[key] = value
            facts.append(f"{label}: {self._format(value)}.")

        table_facts = self._table_summaries(raw)
        facts.extend(table_facts)
        expectations_projection = self._expectations_projection(selected)
        if expectations_projection["text"]:
            facts.append(expectations_projection["text"])
        if not facts:
            warnings = list(raw.get("warnings") or [])
            warnings.append("No usable Yahoo analyst or expectations fields returned")
            return EvidenceFetchResult(
                provider=self.provider_name,
                ticker=request.ticker,
                status=EvidenceFetchStatus.NO_DATA,
                items=[],
                warnings=list(dict.fromkeys(str(x) for x in warnings)),
                fetched_at=fetched_at,
            )

        text = "Analyst expectations snapshot. " + " ".join(facts)
        digest = sha256(
            f"{request.ticker}|{fetched_at.isoformat()}|{text}".encode("utf-8")
        ).hexdigest()[:16]
        evidence_id = f"EVID-YANALYST-{request.ticker}-{digest}"
        source_id = f"SRC-YANALYST-{request.ticker}-{digest}"
        warnings = list(dict.fromkeys(str(x) for x in raw.get("warnings") or []))
        metadata = {
            "ticker": request.ticker,
            "field_names": sorted(selected),
            "field_count": len(selected),
            "expectations_scorable": expectations_projection["scorable"],
            "expectations_projection": expectations_projection["values"],
            "immutable_as_of": fetched_at.isoformat(),
            "policy_version": ANALYST_EVIDENCE_POLICY_VERSION,
        }
        source = EvidenceSource(
            source_id=source_id,
            provider=self.provider_name,
            source_type="ANALYST",
            source_name="Yahoo Finance analyst expectations",
            source_url=f"https://finance.yahoo.com/quote/{request.ticker}/analysis",
            retrieved_at=fetched_at,
            published_at=fetched_at,
            metadata=metadata,
        )
        item = EvidenceItem(
            evidence=ResearchEvidence(
                evidence_id=evidence_id,
                source_type="ANALYST",
                text=text,
                published_at=fetched_at,
                metadata={
                    "provider": self.provider_name,
                    "source_id": source_id,
                    **metadata,
                },
            ),
            source=source,
            kind=EvidenceKind.ANALYST,
            ticker=request.ticker,
        )
        return EvidenceFetchResult(
            provider=self.provider_name,
            ticker=request.ticker,
            status=(
                EvidenceFetchStatus.PARTIAL
                if warnings else EvidenceFetchStatus.SUCCESS
            ),
            items=[item],
            warnings=warnings,
            fetched_at=fetched_at,
            metadata={
                "field_count": len(selected),
                "expectations_scorable": expectations_projection["scorable"],
            },
        )

    @classmethod
    def _expectations_projection(cls, selected: dict[str, Any]) -> dict[str, Any]:
        """Expose price-target comparisons without assigning a priced-in class."""
        current = cls._finite_number(selected.get("currentPrice"))
        values: dict[str, float] = {}
        parts: list[str] = []
        if current is not None and current > 0:
            values["currentPrice"] = current
            for key in _PRICE_TARGET_FIELDS:
                target = cls._finite_number(selected.get(key))
                if target is None:
                    continue
                values[key] = target
                implied = ((target / current) - 1.0) * 100.0
                values[f"{key}ImpliedReturnPct"] = implied
                label = key.removeprefix("target").removesuffix("Price").lower()
                parts.append(f"{label}={implied:.4g}%")
        scorable = bool(current is not None and current > 0 and parts)
        text = (
            "Analyst price-target implied return versus current price: "
            + ", ".join(parts)
            + "."
            if scorable else ""
        )
        return {"scorable": scorable, "values": values, "text": text}

    @staticmethod
    def _default_snapshot_loader(ticker: str) -> dict[str, Any]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise EvidenceProviderResponseError(
                "yfinance is required for YahooAnalystEvidenceProvider"
            ) from exc
        value = yf.Ticker(ticker)
        output: dict[str, Any] = {"warnings": []}
        for name in (
            "info",
            "analyst_price_targets",
            "earnings_estimate",
            "revenue_estimate",
            "eps_revisions",
        ):
            try:
                output[name] = getattr(value, name)
            except Exception as exc:
                output["warnings"].append(
                    f"{name} unavailable: {type(exc).__name__}: {str(exc)[:160]}"
                )
        return output

    @classmethod
    def _table_summaries(cls, raw: dict[str, Any]) -> list[str]:
        output: list[str] = []
        labels = {
            "earnings_estimate": "Analyst earnings estimate",
            "revenue_estimate": "Analyst revenue estimate",
            "eps_revisions": "Analyst EPS revisions",
        }
        for key, label in labels.items():
            value = raw.get(key)
            if value is None or not hasattr(value, "empty") or value.empty:
                continue
            try:
                row = value.iloc[0]
                cells = []
                for name, cell in row.items():
                    scalar = cls._scalar(cell)
                    if scalar is not None:
                        cells.append(f"{name}={cls._format(scalar)}")
                    if len(cells) >= 6:
                        break
                if cells:
                    output.append(f"{label}: " + ", ".join(cells) + ".")
            except Exception:
                continue
        return output

    @staticmethod
    def _clock(request: EvidenceRequest) -> datetime:
        value = request.as_of or datetime.now(timezone.utc)
        if value.tzinfo is None:
            raise ValueError("evidence clock must be timezone-aware")
        return value.astimezone(timezone.utc)

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
    def _finite_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @staticmethod
    def _format(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)
