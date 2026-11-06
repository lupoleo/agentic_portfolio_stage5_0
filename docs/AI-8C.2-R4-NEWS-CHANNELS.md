# AI-8C.2-R4 — Yahoo News Through Three Channels

| Field | Value |
| --- | --- |
| Type | Revision of a reopened contract (AI-8C.2 evidence: news provider) |
| Also affected | E2E-S4.0A.2 news-sensitive frontier (aliases, request metadata) |
| Branch | `ai-8c2-r4-news-channels` |
| Status | ACCEPTED 2026-10-05 |
| Approved by | Operator, 2026-10-05 |

## 1. Reason

From 2026-10-04 the Yahoo ticker news feed (`yfinance.Ticker(...).news`)
returned nothing for every symbol, including AAPL and MSFT, with yfinance
1.6.0 and 1.7.0 alike. yfinance logs "Failed to retrieve the news and received
faulty response instead" and returns an empty list, so the system read the
outage as "no news": the frontier ranking screened 480 listings, qualified
none, kept its circuit closed and fell back to seeded order, spending both
waves on illiquid listings; every research bundle lacked news.

Operator diagnostics on 2026-10-05 showed that Yahoo search still works from
the same machine: `yfinance.Search(symbol)` returned about 20 current items for
US listings and none for `.MI`/`.DE` symbols, while `Search(company name)`
returned 8–14 headlines naming the company for UniCredit, Banco BPM, SAP, BMW
and Eni (including Monte Paschi's bids for Banco BPM). Search items are tagged
broadly (`relatedTickers` included an Nvidia article for AAPL), so a relevance
filter is required.

## 2. Change

1. The news provider tries, in order: the ticker feed (still authoritative
   when it returns items); Yahoo search by symbol, which also yields the
   company's short and long names; Yahoo search by company name when the
   symbol search found nothing relevant.
2. Search items are kept only if the symbol, the base symbol or the normalized
   company name appears as a whole word in the headline or summary. Company
   names lose legal forms and share classes ("UniCredit S.p.A." → "UniCredit",
   "Drägerwerk AG & Co. KGaA (pref)" → "Drägerwerk").
3. A failed ticker feed is detected from yfinance's error log. If every
   attempted channel fails, the provider raises a provider error (frontier:
   `NEWS_PROVIDER_UNAVAILABLE`, which feeds its circuit breaker) instead of
   reporting no news. A failed feed with a reachable but empty search stays
   `NO_DATA`, with the channel failure in the warnings.
4. Every news item records `news_channel` (`TICKER_FEED`, `SEARCH_SYMBOL`,
   `SEARCH_NAME`); result metadata records the state of each channel.
   Selection policy `yahoo-news-selection-v3-multichannel`.
5. The frontier ranking passes the listing's names to the provider and adds
   normalized names to its own direct-mention aliases.

Unchanged: the evidence item format, the 30-day window, canonical ordering and
deduplication, the frontier event classifier and thresholds, research and
scoring contracts. Providers built with an injected feed loader keep the
single-channel behaviour (tests and replays).

## 3. Impact

US listings regain news immediately through symbol search; European listings
gain news through name search. A listing now costs up to three Yahoo calls
instead of one when the feed is down. Persisted evidence is not rewritten;
new evidence IDs follow the existing content-addressed rule.

## 4. Residual risk

Search coverage and tagging are controlled by Yahoo and may change again.
Whole-word matching keeps passing mentions ("now available on SAP Store");
the frontier event classifier and research decide materiality. Headlines in
Italian or German are retrieved but the event classifier's vocabulary is
English.

## 5. Tests

`tests/test_ai_news_multichannel.py` (18 tests): name normalization, whole-word
matching (Eni vs "senior"), feed preferred when it works, feed failure detected
from the yfinance log, symbol-search relevance filter (Nvidia article dropped
for AAPL), European name search from quotes (UniCredit), request names first
(Banco BPM), all channels down raising a provider error the frontier treats as
unavailable, reachable-but-empty search as `NO_DATA`, legacy single channel,
frontier aliases. Regression: 1,789 tests and 162 subtests passed.

## 6. Acceptance criteria

1. Offline regression green (CI).
2. Live provider check on the operator machine returns news items for US and
   European listings with their channel.
3. One LIVE replenishment in which the frontier ranking is `MIXED` with
   qualified listings, research bundles contain NEWS evidence, and side effects
   are zero.

## 7. Live provider check and follow-up fix (2026-10-05)

Operator check with the first R4 commit: AAPL 4 and MSFT 7 items
(`SEARCH_SYMBOL`), ENI.MI 13 (`SEARCH_NAME`), but UCG.MI, BAMI.MI, SAP.DE and
BMW.DE returned none when the name came only from Yahoo quotes. With the
scanner's names UCG.MI (10) and SAP.DE (12) succeeded. Causes found in the
quotes: short names are upper case and padded with a share-class letter
("SAP SE                        I", "BAYERISCHE MOTOREN WERKE AG   S"), and the
full legal name returns no news for BMW.

Follow-up: short names drop the trailing class letter and upper-case names
are searched in title case; listings outside the US also try their base
symbol ("BMW", "SAP", "ENI") when the name search finds nothing relevant
(`SEARCH_BASE_SYMBOL`); a `NO_DATA` caused by relevant items outside the
30-day window now reports the channels too.

## 8. Acceptance evidence (2026-10-05)

Provider check without scanner names (the research condition), after the
follow-up fix: AAPL 4 and MSFT 7 items (`SEARCH_SYMBOL`); UCG.MI 10, SAP.DE 12,
BMW.DE 8 and ENI.MI 13 (`SEARCH_NAME`); BAMI.MI none, its relevant items being
older than the 30-day window. Before R4: news only for AAPL, MSFT and ENI.MI.

Live replenishment `s4a1-75b0cf0919ffd3f8dbfc83f2` on root
`s4a-53afc7eaa122d144cb96f16f` (portfolio snapshot of 2026-10-04), production
database, two waves:

| Measure | 2026-10-04 (feed down, before R4) | 2026-10-05 (R4) |
| --- | --- | --- |
| Frontier ranking mode | `SEEDED_FALLBACK` | `MIXED` |
| Qualified by news | 0 of 480 | 40 of 254 |
| Fallback listings | 48 | 8 |
| Wave 1 | NASDAQ:KMDA, XETRA:1F80 (blocked: liquidity, prices) | BIT:ENEL, BIT:LDO |
| Wave 2 | NYSE:CNF, XETRA:DAM (blocked/review) | NASDAQ:GIFT, XETRA:4AB |

Research bundles of ENEL.MI and LDO.MI carried 9–10 NEWS items each and no
news warning; all AI-8C.2 and AI-8C.3 checks passed; no processing failure;
zero broker orders, portfolio mutations and automatic executions. All four
research were `PARTIAL`; confidence-adjusted scores 51.0–53.9.

Findings for follow-up:

- The remaining blocking gaps are mostly forward-looking items filed as
  unknowns ("impact of M&A activity on long-term margins", "exact future
  earnings trajectory") and sector multiples phrased without a comparison
  marker ("sector-specific pricing multiples not disclosed").
- ENEL.MI LONG and SHORT received two company assessments (identical values)
  because the analyst snapshot's current price changed between the two
  bundles, changing the content-addressed evidence IDs used as cache key.
