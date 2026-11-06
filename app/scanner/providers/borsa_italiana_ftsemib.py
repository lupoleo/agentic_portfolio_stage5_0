from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from html.parser import HTMLParser
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from app.scanner.universe_models import RawMarketListing

FTSE_MIB_PAGE_1 = "https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html"
FTSE_MIB_PAGE_2 = "https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html?page=2"
BORSA_ITALIANA_BASE = "https://www.borsaitaliana.it"
PROVIDER_ID = "borsa-italiana-ftse-mib"
PROVIDER_VERSION = "1"
SOURCE_NAME = "Borsa Italiana FTSE MIB"
CANONICAL_EXCHANGE = "BIT"
CANONICAL_MARKET = "EURONEXT_MILAN"
CANONICAL_REGION = "EUROPE"
CANONICAL_CURRENCY = "EUR"
CANONICAL_INSTRUMENT_TYPE = "COMMON_STOCK"
_ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")
_DETAIL_HREF_RE = re.compile(r"^/borsa/azioni/scheda/(?P<isin>[A-Z]{2}[A-Z0-9]{9}[0-9])-[A-Z0-9]+\.html", re.I)
_TBODY_RE = re.compile(
    r"<tbody\b[^>]*>.*?</tbody\s*>",
    re.IGNORECASE | re.DOTALL,
    )
_TABLE_RE = re.compile(
    r"<table\b[^>]*>.*?</table\s*>",
    re.IGNORECASE | re.DOTALL,
    )
_ALLOWED_MARKETS = frozenset({
    "euronext milan",
    "euronext star milan",
})
def _clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split()).strip()

def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    value = _clean_text(value)
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    return value

class BorsaItalianaProviderStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"

@dataclass(frozen=True)
class BorsaItalianaDiagnostic:
    code: str
    message: str
    isin: str | None = None
    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required_text(self.code, "code").upper())
        object.__setattr__(self, "message", _required_text(self.message, "message"))
        if self.isin is not None:
            object.__setattr__(self, "isin", _required_text(self.isin, "isin").upper())

@dataclass(frozen=True)
class FTSEMIBConstituentReference:
    isin: str
    name: str
    detail_url: str
    def __post_init__(self) -> None:
        isin = _required_text(self.isin, "isin").upper()
        if not _ISIN_RE.fullmatch(isin):
            raise ValueError(f"invalid ISIN: {isin!r}")
        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(self, "detail_url", _required_text(self.detail_url, "detail_url"))

@dataclass(frozen=True)
class BorsaItalianaFTSEMIBResult:
    provider_id: str
    provider_version: str
    source_name: str
    source_urls: tuple[str, ...]
    status: BorsaItalianaProviderStatus
    listings: tuple[RawMarketListing, ...] = ()
    diagnostics: tuple[BorsaItalianaDiagnostic, ...] = ()
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.provider_version.strip() or not self.source_name.strip():
            raise ValueError("provider identity fields must not be blank")
        if not self.source_urls:
            raise ValueError("source_urls must not be empty")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if self.status is BorsaItalianaProviderStatus.SUCCESS and not self.listings:
            raise ValueError("SUCCESS requires listings")
        if self.status is BorsaItalianaProviderStatus.PARTIAL and (not self.listings or not self.diagnostics):
            raise ValueError("PARTIAL requires listings and diagnostics")
        if self.status is BorsaItalianaProviderStatus.FAILED and (self.listings or not self.diagnostics):
            raise ValueError("FAILED requires diagnostics and no listings")

class _ConstituentPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[FTSEMIBConstituentReference] = []
        self._href = None; self._isin = None; self._text: list[str] = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a": return
        href = dict(attrs).get("href")
        if not href: return
        m = _DETAIL_HREF_RE.match(href)
        if not m: return
        self._href = href; self._isin = m.group("isin").upper(); self._text = []
    def handle_data(self, data):
        if self._href is not None: self._text.append(data)
    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._href is None: return
        name = _clean_text(" ".join(self._text))
        if name and self._isin:
            self.references.append(FTSEMIBConstituentReference(self._isin, name, urljoin(BORSA_ITALIANA_BASE, self._href)))
        self._href = self._isin = None; self._text = []

class _InstrumentDetailParser(HTMLParser):
    LABELS = {"Codice Isin": "isin", "Codice Alfanumerico": "symbol", "Mercato/Segmento": "market"}
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True); self.parts: list[str] = []
    def handle_data(self, data):
        v = _clean_text(data)
        if v: self.parts.append(v)
    def semantic_values(self) -> dict[str, str]:
        out = {}
        for i, value in enumerate(self.parts):
            field = self.LABELS.get(value)
            if field and i + 1 < len(self.parts): out[field] = self.parts[i + 1]
        return out

def parse_constituent_page(
    html: str,
) -> tuple[FTSEMIBConstituentReference, ...]:
    # The complete Borsa Italiana document contains upstream markup that can
    # leave Python's HTMLParser inside a script-like state. Parse table bodies
    # independently so malformed unrelated markup cannot hide constituents.
    fragments = _TBODY_RE.findall(html)
    if not fragments:
        fragments = [html]

    references: list[FTSEMIBConstituentReference] = []

    for fragment in fragments:
        parser = _ConstituentPageParser()
        parser.feed(fragment)
        parser.close()
        references.extend(parser.references)

    by_isin: dict[str, FTSEMIBConstituentReference] = {}

    for ref in references:
        previous = by_isin.get(ref.isin)

        if previous and (
            previous.detail_url != ref.detail_url
            or previous.name != ref.name
        ):
            raise ValueError(
                f"conflicting duplicate constituent ISIN {ref.isin}"
            )

        by_isin[ref.isin] = ref

    return tuple(sorted(by_isin.values(), key=lambda item: item.isin))

def parse_instrument_detail(
    html: str,
    *,
    expected_isin: str,
) -> tuple[str, str, str]:
    # Parse tables independently because malformed markup earlier in the
    # document can leave HTMLParser in a script-like state.
    fragments = _TABLE_RE.findall(html)
    if not fragments:
        fragments = [html]

    values: dict[str, str] = {}

    for fragment in fragments:
        parser = _InstrumentDetailParser()
        parser.feed(fragment)
        parser.close()
        values.update(parser.semantic_values())

    isin = _required_text(
        values.get("isin"),
        "Codice Isin",
    ).upper()

    symbol = _required_text(
        values.get("symbol"),
        "Codice Alfanumerico",
    ).upper()

    market = _required_text(
        values.get("market"),
        "Mercato/Segmento",
    )

    if isin != expected_isin.upper():
        raise ValueError(
            f"detail ISIN mismatch: expected {expected_isin.upper()}, "
            f"got {isin}"
        )

    if market.casefold() not in _ALLOWED_MARKETS:
        raise ValueError(
            f"unexpected market/segment: {market}"
    )

    return isin, symbol, market

class BorsaItalianaFTSEMIBProvider:
    def __init__(self, *, timeout_seconds: float = 20.0, opener: Callable[..., Any] = urlopen,
                 expected_min_constituents: int = 38, expected_max_constituents: int = 42) -> None:
        if timeout_seconds <= 0: raise ValueError("timeout_seconds must be > 0")
        if expected_min_constituents <= 0 or expected_max_constituents < expected_min_constituents:
            raise ValueError("invalid expected constituent range")
        self._timeout = float(timeout_seconds); self._opener = opener
        self._expected_min = expected_min_constituents; self._expected_max = expected_max_constituents
    def fetch(self) -> BorsaItalianaFTSEMIBResult:
        try:
            p1 = self._fetch_text(FTSE_MIB_PAGE_1); p2 = self._fetch_text(FTSE_MIB_PAGE_2)
        except Exception as exc:
            return self._failed("MEMBERSHIP_FETCH_FAILED", self._safe_error(exc))
        try:
            refs = self._merge(parse_constituent_page(p1), parse_constituent_page(p2))
        except ValueError as exc:
            return self._failed("MEMBERSHIP_PARSE_FAILED", str(exc))
        if not (self._expected_min <= len(refs) <= self._expected_max):
            return self._failed("IMPLAUSIBLE_CONSTITUENT_COUNT", f"FTSE MIB constituent count {len(refs)} outside expected range {self._expected_min}..{self._expected_max}")
        diagnostics = []; listings = []
        for ref in refs:
            try:
                isin, symbol, _ = parse_instrument_detail(self._fetch_text(ref.detail_url), expected_isin=ref.isin)
                listings.append(RawMarketListing(symbol=symbol, exchange=CANONICAL_EXCHANGE, market=CANONICAL_MARKET,
                    region=CANONICAL_REGION, currency=CANONICAL_CURRENCY, instrument_type=CANONICAL_INSTRUMENT_TYPE,
                    isin=isin, name=ref.name, country="IT", source=SOURCE_NAME))
            except Exception as exc:
                diagnostics.append(BorsaItalianaDiagnostic("DETAIL_REJECTED", self._safe_error(exc), ref.isin))
        if self._duplicates(x.isin for x in listings if x.isin):
            return self._failed("DUPLICATE_ISIN", "duplicate resolved ISIN")
        if self._duplicates(x.symbol for x in listings):
            return self._failed("DUPLICATE_SYMBOL", "duplicate resolved symbol")
        if not listings: return self._failed("NO_VALID_LISTINGS", "no FTSE MIB listings could be resolved")
        listings.sort(key=lambda x: x.symbol)
        status = BorsaItalianaProviderStatus.PARTIAL if diagnostics else BorsaItalianaProviderStatus.SUCCESS
        metadata = {"membership_reference_count": len(refs), "resolved_listing_count": len(listings),
            "rejected_detail_count": len(diagnostics), "expected_min_constituents": self._expected_min,
            "expected_max_constituents": self._expected_max, "exchange": CANONICAL_EXCHANGE,
            "market": CANONICAL_MARKET, "region": CANONICAL_REGION}
        return BorsaItalianaFTSEMIBResult(PROVIDER_ID, PROVIDER_VERSION, SOURCE_NAME, (FTSE_MIB_PAGE_1, FTSE_MIB_PAGE_2),
            status, tuple(listings), tuple(diagnostics), metadata=metadata)
    def _fetch_text(self, url: str) -> str:
        req = Request(url, headers={"User-Agent": "AgenticPortfolio/1.0 (FTSE-MIB universe adapter)"})
        with self._opener(req, timeout=self._timeout) as response: payload = response.read()
        return payload.decode("utf-8", errors="strict")
    @staticmethod
    def _merge(*groups):
        merged = {}
        for group in groups:
            for ref in group:
                prev = merged.get(ref.isin)
                if prev and (prev.name != ref.name or prev.detail_url != ref.detail_url):
                    raise ValueError(f"conflicting duplicate constituent ISIN {ref.isin}")
                merged[ref.isin] = ref
        return tuple(sorted(merged.values(), key=lambda x: x.isin))
    @staticmethod
    def _duplicates(values):
        seen=set(); dup=set()
        for v in values:
            if v in seen: dup.add(v)
            seen.add(v)
        return sorted(dup)
    @staticmethod
    def _safe_error(exc):
        if isinstance(exc, HTTPError): return f"HTTP {exc.code}"
        if isinstance(exc, URLError): return f"network error: {exc.reason}"
        return f"{type(exc).__name__}: {exc}"
    @staticmethod
    def _failed(code, message):
        return BorsaItalianaFTSEMIBResult(PROVIDER_ID, PROVIDER_VERSION, SOURCE_NAME, (FTSE_MIB_PAGE_1, FTSE_MIB_PAGE_2),
            BorsaItalianaProviderStatus.FAILED, diagnostics=(BorsaItalianaDiagnostic(code, message),))
