# E2E-S2.1D-IT — Borsa Italiana FTSE MIB Provider

Official-source adapter for the Italian Scanner V1 universe because EODHD does not currently cover the Italian market.

Sources:
- `https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html`
- `https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html?page=2`

Robustness: no positional table scraping and no CSS-class dependency. Membership is recognized from official equity-detail URLs containing the ISIN. Detail pages are parsed by the semantic labels `Codice Isin`, `Codice Alfanumerico`, and `Mercato/Segmento`; the market must equal `Euronext Milan` or `Euronext STAR Milan`.

Canonical `RawMarketListing` output: `exchange=BIT`, `market=EURONEXT_MILAN`, `region=EUROPE`, `currency=EUR`, `instrument_type=COMMON_STOCK`, `country=IT`. Yahoo `.MI` translation remains downstream.

Invariants: plausible membership count 38..42, detail ISIN must match membership ISIN, market must be Euronext Milan or Euronext STAR Milan, nonblank official symbol, unique resolved ISINs and symbols, and explicit PARTIAL/FAILED states rather than silent corruption.

V1 fetches two membership pages plus one detail page per constituent. S2.1G cache/freshness will prevent this path from running on every scanner execution.
