from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CanonicalInstrumentType(str, Enum):
    COMMON_STOCK = "COMMON_STOCK"
    PREFERRED_STOCK = "PREFERRED_STOCK"
    ETF = "ETF"
    ETC = "ETC"
    FUND = "FUND"
    MUTUAL_FUND = "MUTUAL_FUND"
    BOND = "BOND"
    NOTE = "NOTE"
    WARRANT = "WARRANT"
    UNIT = "UNIT"
    INDEX = "INDEX"
    UNKNOWN = "UNKNOWN"


RAW_TYPE_MAP = {
    "COMMON STOCK": CanonicalInstrumentType.COMMON_STOCK,
    "COMMON_STOCK": CanonicalInstrumentType.COMMON_STOCK,
    "PREFERRED STOCK": CanonicalInstrumentType.PREFERRED_STOCK,
    "ETF": CanonicalInstrumentType.ETF,
    "ETC": CanonicalInstrumentType.ETC,
    "FUND": CanonicalInstrumentType.FUND,
    "MUTUAL FUND": CanonicalInstrumentType.MUTUAL_FUND,
    "BOND": CanonicalInstrumentType.BOND,
    "NOTE": CanonicalInstrumentType.NOTE,
    "NOTES": CanonicalInstrumentType.NOTE,
    "WARRANT": CanonicalInstrumentType.WARRANT,
    "UNIT": CanonicalInstrumentType.UNIT,
    "INDEX": CanonicalInstrumentType.INDEX,
}


@dataclass(frozen=True)
class InstrumentTypeResolution:
    raw_value: str | None
    normalized_raw_value: str | None
    canonical_type: CanonicalInstrumentType
    recognized: bool


def normalize_raw_instrument_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().upper().split())
    return normalized or None


def resolve_instrument_type(
    value: str | None,
) -> InstrumentTypeResolution:
    normalized = normalize_raw_instrument_type(value)
    canonical = RAW_TYPE_MAP.get(
        normalized,
        CanonicalInstrumentType.UNKNOWN,
    )
    return InstrumentTypeResolution(
        raw_value=value,
        normalized_raw_value=normalized,
        canonical_type=canonical,
        recognized=canonical is not CanonicalInstrumentType.UNKNOWN,
    )
