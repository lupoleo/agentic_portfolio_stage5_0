from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import json
from pathlib import Path

from app.portfolio.models import PortfolioPosition


DEFAULT_REFERENCE_PATH = Path(
    "config/portfolio/instrument_references_v1.json"
)


@dataclass(frozen=True)
class InstrumentReference:
    reference_id: str
    isin: str
    broker_symbol: str
    market_data_symbol: str
    market_data_method: str
    economic_direction: str | None
    leverage_multiplier: float
    expiry_date: date | None
    reviewed: bool

    @classmethod
    def from_dict(cls, value: dict) -> "InstrumentReference":
        expiry = value.get("expiry_date")
        result = cls(
            reference_id=str(value["reference_id"]),
            isin=str(value.get("isin") or "").strip().upper(),
            broker_symbol=str(value.get("broker_symbol") or "").strip().upper(),
            market_data_symbol=str(value["market_data_symbol"]).strip().upper(),
            market_data_method=str(value["market_data_method"]).strip().upper(),
            economic_direction=(
                str(value["economic_direction"]).strip().upper()
                if value.get("economic_direction")
                else None
            ),
            leverage_multiplier=float(value.get("leverage_multiplier", 1.0)),
            expiry_date=date.fromisoformat(expiry) if expiry else None,
            reviewed=bool(value.get("reviewed", False)),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if not self.reviewed:
            raise ValueError(f"Unreviewed instrument reference: {self.reference_id}")
        if not self.isin and not self.broker_symbol:
            raise ValueError("Reference requires ISIN or broker_symbol")
        if self.market_data_method not in {"DIRECT", "LEVERAGED_PROXY"}:
            raise ValueError(f"Unsupported market_data_method: {self.market_data_method}")
        if self.economic_direction not in {None, "LONG", "SHORT"}:
            raise ValueError("economic_direction must be LONG, SHORT or null")
        if self.leverage_multiplier <= 0:
            raise ValueError("leverage_multiplier must be positive")
        if self.market_data_method == "DIRECT" and self.leverage_multiplier != 1.0:
            raise ValueError("DIRECT references cannot multiply market returns")


class InstrumentReferenceRegistry:
    def __init__(self, references: tuple[InstrumentReference, ...]):
        self.references = references
        self._by_isin = {r.isin: r for r in references if r.isin}
        self._by_symbol = {r.broker_symbol: r for r in references if r.broker_symbol}
        if len(self._by_isin) != sum(bool(r.isin) for r in references):
            raise ValueError("Duplicate ISIN in instrument references")
        if len(self._by_symbol) != sum(bool(r.broker_symbol) for r in references):
            raise ValueError("Duplicate broker_symbol in instrument references")

    @classmethod
    def load(cls, path: str | Path = DEFAULT_REFERENCE_PATH) -> "InstrumentReferenceRegistry":
        source = Path(path)
        if not source.exists():
            return cls(())
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload.get("schema") != "portfolio-instrument-references-v1":
            raise ValueError("Unsupported instrument-reference schema")
        return cls(tuple(InstrumentReference.from_dict(x) for x in payload["references"]))

    def resolve(self, position: PortfolioPosition, *, as_of: date | None = None) -> InstrumentReference | None:
        isin = str(position.isin or "").strip().upper()
        symbol = str(position.broker_symbol or "").strip().upper()
        by_isin = self._by_isin.get(isin) if isin not in {"", "NAN"} else None
        by_symbol = self._by_symbol.get(symbol)
        if by_isin is not None and by_symbol is not None and by_isin != by_symbol:
            raise ValueError(f"Conflicting instrument references for {isin}/{symbol}")
        reference = by_isin or by_symbol
        if reference is None:
            return None
        effective_date = as_of or date.today()
        if reference.expiry_date is not None and effective_date > reference.expiry_date:
            raise ValueError(f"Instrument reference expired: {reference.reference_id}")
        return reference


def enrich_position(
    position: PortfolioPosition,
    *,
    registry: InstrumentReferenceRegistry | None = None,
    as_of: date | None = None,
) -> PortfolioPosition:
    registry = registry or InstrumentReferenceRegistry.load()
    reference = registry.resolve(position, as_of=as_of)
    if reference is None:
        return position
    return replace(
        position,
        economic_direction_override=reference.economic_direction,
        market_data_symbol=reference.market_data_symbol,
        market_data_method=reference.market_data_method,
        leverage_multiplier=reference.leverage_multiplier,
        instrument_reference_id=reference.reference_id,
        history_is_real_product_price=(reference.market_data_method == "DIRECT"),
    )
