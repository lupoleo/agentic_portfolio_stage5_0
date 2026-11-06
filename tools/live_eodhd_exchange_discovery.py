from __future__ import annotations

import sys

from app.scanner.eodhd_exchange_discovery import (
    EODHDExchangeDiscoveryProvider,
    ExchangeDiscoveryStatus,
)


def main() -> int:
    try:
        provider = EODHDExchangeDiscoveryProvider()
    except ValueError as exc:
        print(f"configuration error: {exc}")
        return 2

    result = provider.discover()

    print(f"provider: {result.provider_id} v{result.provider_version}")
    print(f"source: {result.source_url}")
    print(f"status: {result.status.value}")
    print(f"exchanges: {len(result.exchanges)}")

    if result.metadata:
        print(
            "counts: "
            f"physical={result.metadata.get('physical_exchange_count', 0)} "
            f"virtual={result.metadata.get('virtual_exchange_count', 0)} "
            f"US={result.metadata.get('us_exchange_count', 0)} "
            f"EUROPE={result.metadata.get('europe_exchange_count', 0)} "
            f"OTHER={result.metadata.get('other_exchange_count', 0)}"
        )

    if result.diagnostics:
        print("diagnostics:")
        for diagnostic in result.diagnostics:
            exchange = (
                f" [{diagnostic.exchange_code}]"
                if diagnostic.exchange_code
                else ""
            )
            print(f"  {diagnostic.code}{exchange}: {diagnostic.message}")

    if result.status == ExchangeDiscoveryStatus.FAILED:
        return 1

    print()
    print("US / EUROPE physical exchange candidates:")
    for item in result.exchanges:
        if item.is_virtual or item.region not in {"US", "EUROPE"}:
            continue
        mic = item.operating_mic or "-"
        print(
            f"  {item.code:10} {item.region:7} "
            f"{item.country_iso2 or '-':2} {item.currency:7} "
            f"MIC={mic:20} {item.name}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
