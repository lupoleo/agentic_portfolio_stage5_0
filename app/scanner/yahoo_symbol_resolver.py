"""Offline candidate mapping only: no identity or availability claims."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Iterable

from app.scanner.market_data_contracts import (
    MarketDataDiagnostic, SymbolMappingResult, SymbolMappingStatus,
)
from app.scanner.universe_models import ListingKey, MarketListing


YAHOO_RULESET_VERSION = "scanner-yahoo-symbols-v1"
# Initial candidate rules; live verification is mandatory downstream.
YAHOO_VENUE_SUFFIXES = MappingProxyType({
    "AMEX": "", "BATS": "", "NASDAQ": "", "NYSE": "", "NYSE ARCA": "",
    "AS": ".AS", "AT": ".AT", "BIT": ".MI", "BR": ".BR", "BUD": ".BD",
    "CO": ".CO", "HE": ".HE", "LS": ".LS", "LSE": ".L", "MC": ".MC",
    "OL": ".OL", "PA": ".PA", "PR": ".PR", "ST": ".ST", "SW": ".SW",
    "VI": ".VI", "WAR": ".WA", "XETRA": ".DE",
})
_PLAIN = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", re.ASCII)
_US_CLASS = re.compile(r"([A-Z0-9]+)\.([AB])", re.ASCII)
_NORDIC_CLASS = re.compile(r"([A-Z0-9]+)[ .]([AB])", re.ASCII)


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonblank text")
    return value.strip()


@dataclass(frozen=True)
class YahooSymbolOverride:
    """One exact key, with provenance; empty candidates explicitly block it.

    More than one distinct candidate represents unresolved ambiguity. The
    override records mapping evidence, not a completed identity verification.
    """
    listing_key: ListingKey
    candidate_symbols: tuple[str, ...]
    reference: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.listing_key, ListingKey):
            raise ValueError("listing_key must be a ListingKey")
        if isinstance(self.candidate_symbols, str):
            raise ValueError("candidate_symbols must be a sequence, not text")
        suffix = YAHOO_VENUE_SUFFIXES.get(self.listing_key.exchange)
        if suffix is None:
            raise ValueError("override venue needs a supported suffix rule first")
        candidates = tuple(sorted(set(
            _text(s, "candidate symbol").upper() for s in self.candidate_symbols
        )))
        for symbol in candidates:
            if suffix:
                if not symbol.endswith(suffix):
                    raise ValueError("override must preserve the listing venue suffix")
                stem = symbol[:-len(suffix)]
            else:
                stem = symbol
            if not _PLAIN.fullmatch(stem):
                raise ValueError("invalid override symbol or foreign venue suffix")
        object.__setattr__(self, "candidate_symbols", candidates)
        object.__setattr__(self, "reference", _text(self.reference, "reference"))
        object.__setattr__(self, "reason", _text(self.reason, "reason"))


class YahooSymbolResolver:
    """Deterministic resolver; does not import yfinance or Fineco adapters.

    Overrides are immutable snapshots. Their complete content participates in
    mapping_version so changes invalidate downstream cached verification.
    """

    def __init__(self, overrides: Iterable[YahooSymbolOverride] = ()) -> None:
        entries = {}
        for item in overrides:
            if not isinstance(item, YahooSymbolOverride):
                raise ValueError("invalid override")
            if item.listing_key in entries:
                raise ValueError("duplicate override key")
            entries[item.listing_key] = item
        self._overrides = MappingProxyType(entries)
        payload = [
            [key.exchange, key.symbol, item.candidate_symbols,
             item.reference, item.reason]
            for key, item in sorted(entries.items())
        ]
        digest = sha256(json.dumps(payload, separators=(",", ":"),
                                   ensure_ascii=True).encode("utf-8")).hexdigest()
        self._mapping_version = f"{YAHOO_RULESET_VERSION}:{digest}"

    @property
    def mapping_version(self) -> str:
        return self._mapping_version

    def _result(self, key, status, candidates=(), *, rule=None, code=None,
                message=None):
        diagnostics = () if code is None else (MarketDataDiagnostic(code, message),)
        return SymbolMappingResult(
            listing_key=key, provider_id="yahoo",
            mapping_version=self.mapping_version, status=status,
            candidate_symbols=candidates, rule_id=rule, diagnostics=diagnostics,
        )

    def resolve(self, listing: MarketListing | ListingKey) -> SymbolMappingResult:
        # Typed inputs already validate nonblank keys. Arbitrary objects are
        # programming errors; never invent a placeholder key for them.
        if isinstance(listing, MarketListing):
            key = listing.key
        elif isinstance(listing, ListingKey):
            key = listing
        else:
            raise TypeError("resolve expects MarketListing or ListingKey")

        override = self._overrides.get(key)
        if override is not None:
            count = len(override.candidate_symbols)
            status = (SymbolMappingStatus.RESOLVED if count == 1 else
                      SymbolMappingStatus.AMBIGUOUS if count > 1 else
                      SymbolMappingStatus.UNMAPPED)
            return self._result(
                key, status, override.candidate_symbols, rule="exact-key-override-v1",
                code="EXPLICIT_OVERRIDE",
                message=f"{override.reason}; reference: {override.reference}",
            )

        suffix = YAHOO_VENUE_SUFFIXES.get(key.exchange)
        if suffix is None:
            return self._result(
                key, SymbolMappingStatus.UNMAPPED, code="UNSUPPORTED_VENUE",
                message=f"No candidate rule for {key.exchange}; no venue fallback",
            )
        stem = key.symbol
        transform = "literal"
        if not suffix:
            match = _US_CLASS.fullmatch(stem)
            if match:
                stem = "-".join(match.groups())
                transform = "us-class-dot"
        elif key.exchange in {"ST", "CO"}:
            match = _NORDIC_CLASS.fullmatch(stem)
            if match:
                stem = "-".join(match.groups())
                transform = "nordic-class-separator"

        if not _PLAIN.fullmatch(stem):
            return self._result(
                key, SymbolMappingStatus.UNMAPPED, code="UNSUPPORTED_SYMBOL_FORMAT",
                message="Special syntax or pre-suffixed symbol requires an exact override",
            )
        return self._result(
            key, SymbolMappingStatus.RESOLVED, (stem + suffix,),
            rule=f"{key.exchange}:{transform}:v1",
        )

    def resolve_many(self, listings: Iterable[MarketListing | ListingKey]
                     ) -> tuple[SymbolMappingResult, ...]:
        """Sorted output, exact-key deduplication, collision-safe batch mapping.

        Different source keys resolving to one Yahoo symbol are blocked rather
        than silently merged. Collision detection spans this batch only.
        """
        results = {}
        for listing in listings:
            result = self.resolve(listing)
            results[result.listing_key] = result
        by_symbol = {}
        for key, result in results.items():
            if result.resolved_symbol is not None:
                by_symbol.setdefault(result.resolved_symbol, []).append(key)
        for symbol, keys in by_symbol.items():
            if len(keys) > 1:
                description = ", ".join(f"{k.exchange}:{k.symbol}" for k in sorted(keys))
                for key in keys:
                    results[key] = self._result(
                        key, SymbolMappingStatus.UNMAPPED,
                        rule="batch-collision-guard-v1", code="YAHOO_SYMBOL_COLLISION",
                        message=f"Candidate {symbol} is shared by distinct keys: {description}",
                    )
        return tuple(results[key] for key in sorted(results))
