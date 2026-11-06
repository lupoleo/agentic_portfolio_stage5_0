from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from app.scanner.eodhd_exchange_discovery import (
    EODHDExchangeDiscoveryProvider,
    ExchangeDiscoveryStatus,
)
from app.scanner.exchange_provider import (
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
)
from app.scanner.providers.eodhd_exchange_symbols import (
    EODHDExchangeSymbolProvider,
)


OFFICIAL_BASE_URL = "https://live.deutsche-boerse.com"
EXPECTED_DAX_COUNT = 40
ISIN_RE = re.compile(r"ISIN:\s*([A-Z]{2}[A-Z0-9]{10})")
WKN_RE = re.compile(r"WKN:\s*([^<|]+)")
TICKER_RE = re.compile(r"K(?:ü|&uuml;)rzel:\s*([^<|]+)")
TITLE_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
LINK_RE = re.compile(
    r"\[[^\]]+\]\((https://live\.deutsche-boerse\.com/aktie/[^\s\)\"]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OfficialDaxComponent:
    name: str
    isin: str
    wkn: str
    ticker: str
    source_url: str


@dataclass(frozen=True)
class CoverageRow:
    name: str
    isin: str
    official_ticker: str
    eodhd_symbol: str | None
    eodhd_name: str | None
    instrument_type: str | None
    covered: bool


def _required_match(pattern: re.Pattern[str], text: str, field: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"official detail page missing {field}")
    value = unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
    if not value:
        raise ValueError(f"official detail page has blank {field}")
    return value


def extract_official_links(markdown: str) -> tuple[str, ...]:
    links = tuple(dict.fromkeys(LINK_RE.findall(markdown)))
    if len(links) != EXPECTED_DAX_COUNT:
        raise ValueError(
            f"expected {EXPECTED_DAX_COUNT} official DAX links, got {len(links)}"
        )
    return links


def parse_official_detail(html: str, source_url: str) -> OfficialDaxComponent:
    return OfficialDaxComponent(
        name=_required_match(TITLE_RE, html, "name"),
        isin=_required_match(ISIN_RE, html, "ISIN").upper(),
        wkn=_required_match(WKN_RE, html, "WKN").upper(),
        ticker=_required_match(TICKER_RE, html, "ticker").upper(),
        source_url=source_url,
    )


def fetch_text(url: str, timeout_seconds: float) -> str:
    request = Request(
        url,
        headers={"User-Agent": "AgenticPortfolio/1.0 (DAX coverage audit)"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="strict")


def load_official_components(
    reference_markdown: Path,
    timeout_seconds: float,
) -> tuple[OfficialDaxComponent, ...]:
    links = extract_official_links(reference_markdown.read_text(encoding="utf-8"))
    components: list[OfficialDaxComponent] = []
    for position, link in enumerate(links, start=1):
        url = urljoin(OFFICIAL_BASE_URL, link)
        component = parse_official_detail(fetch_text(url, timeout_seconds), url)
        components.append(component)
        print(
            f"OFFICIAL {position:02d}/{EXPECTED_DAX_COUNT} | "
            f"{component.isin} | {component.ticker} | {component.name}"
        )

    isins = [component.isin for component in components]
    if len(set(isins)) != EXPECTED_DAX_COUNT:
        raise ValueError("official DAX reference contains duplicate ISINs")
    return tuple(components)


def _descriptor_for_xetra(timeout_seconds: float):
    discovery = EODHDExchangeDiscoveryProvider(
        timeout_seconds=timeout_seconds,
    ).discover()
    if discovery.status is ExchangeDiscoveryStatus.FAILED:
        raise RuntimeError(
            "EODHD exchange discovery failed: "
            + "; ".join(item.message for item in discovery.diagnostics)
        )
    matches = [item for item in discovery.exchanges if item.code == "XETRA"]
    if len(matches) != 1:
        raise RuntimeError(f"expected one XETRA descriptor, got {len(matches)}")
    return discovery, matches[0]


def compare_coverage(
    official: Iterable[OfficialDaxComponent],
    eodhd_listings,
) -> tuple[CoverageRow, ...]:
    by_isin: dict[str, list] = {}
    for listing in eodhd_listings:
        if listing.isin:
            by_isin.setdefault(listing.isin, []).append(listing)

    rows: list[CoverageRow] = []
    for component in official:
        matches = by_isin.get(component.isin, [])
        if len(matches) > 1:
            raise ValueError(
                f"ambiguous EODHD XETRA ISIN {component.isin}: {len(matches)} rows"
            )
        listing = matches[0] if matches else None
        rows.append(
            CoverageRow(
                name=component.name,
                isin=component.isin,
                official_ticker=component.ticker,
                eodhd_symbol=listing.symbol if listing else None,
                eodhd_name=listing.name if listing else None,
                instrument_type=listing.instrument_type if listing else None,
                covered=listing is not None,
            )
        )
    return tuple(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit official DAX constituents against the EODHD XETRA universe."
        )
    )
    parser.add_argument(
        "--reference-markdown",
        type=Path,
        default=Path("docs/reference/DAX-XETRA-COMPONENTS-20260918.md"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("data/cache/scanner/exchange_audit"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be > 0")
    if not args.reference_markdown.is_file():
        raise SystemExit(f"reference file not found: {args.reference_markdown}")

    started = time.perf_counter()
    official = load_official_components(
        args.reference_markdown,
        args.timeout_seconds,
    )
    discovery, descriptor = _descriptor_for_xetra(args.timeout_seconds)
    result = EODHDExchangeSymbolProvider(
        descriptor=descriptor,
        timeout_seconds=args.timeout_seconds,
    ).fetch(ExchangeSymbolRequest("XETRA"))
    if result.status is ExchangeProviderStatus.FAILED:
        raise RuntimeError(
            "EODHD XETRA acquisition failed: "
            + "; ".join(item.message for item in result.diagnostics)
        )

    rows = compare_coverage(official, result.listings)
    missing = tuple(row for row in rows if not row.covered)
    covered = len(rows) - len(missing)
    status = "PASS" if not missing else "FAIL"

    print("\nDAX XETRA COVERAGE:")
    print(f"status: {status}")
    print(f"official_components: {len(rows)}")
    print(f"covered_by_eodhd_xetra: {covered}")
    print(f"missing: {len(missing)}")
    print(f"eodhd_provider_status: {result.status.value}")
    print(f"eodhd_xetra_listings: {len(result.listings)}")
    print(f"eodhd_diagnostics: {len(result.diagnostics)}")
    if missing:
        print("\nMISSING:")
        for row in missing:
            print(f"{row.isin} | {row.official_ticker} | {row.name}")

    ticker_differences = [
        row
        for row in rows
        if row.covered and row.eodhd_symbol != row.official_ticker
    ]
    print(f"ticker_differences: {len(ticker_differences)}")
    for row in ticker_differences:
        print(
            f"TICKER | {row.isin} | official={row.official_ticker} | "
            f"eodhd={row.eodhd_symbol}"
        )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "official_source": str(args.reference_markdown),
        "official_component_count": len(rows),
        "covered_component_count": covered,
        "missing_component_count": len(missing),
        "coverage_percent": round(covered / len(rows) * 100.0, 3),
        "eodhd_discovery_status": discovery.status.value,
        "eodhd_provider_status": result.status.value,
        "eodhd_listing_count": len(result.listings),
        "eodhd_diagnostics": [asdict(item) for item in result.diagnostics],
        "ticker_difference_count": len(ticker_differences),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "components": [asdict(row) for row in rows],
    }
    args.output_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_directory / f"dax_xetra_coverage_{stamp}.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"report: {output_path}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
