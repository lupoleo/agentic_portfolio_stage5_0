# E2E-S2.2D — History and Calendar Adapters, EUR Pilot

## Status

Second implementation block. Extends the approved offline history-quality core.
No changes to Portfolio Analysis, S2.2C contracts or the existing verification
cache. Full S2.2D remains open: FX, verified listing-start reference acquisition,
reusable historical-data caching/replay and broader calendar validation remain.

User-confirmed environment: yfinance 1.6.0, pandas 2.3.3,
exchange-calendars 4.13.2 and tzdata 2026.3. User calendar smoke checks produced
timezone-aware open/close schedules for XMIL, XETR and XNYS, exit 0.

## Calendar adapter

`ExchangeSessionCalendarProvider` uses explicit initial bindings:

| Scanner venue | Calendar |
| --- | --- |
| BIT | XMIL |
| XETRA | XETR |
| NYSE | XNYS |

Other venues raise an unsupported-calendar error. In particular NASDAQ is not
silently routed through XNYS. Additional validated mappings can be added later.

The adapter requires exchange-calendars 4.13.2, retained as calendar version.
It builds a padded interval, checks coverage/schema, and converts actual schedule
closes to UTC. Session labels stay exchange trading dates. Holidays are represented
by absent schedule sessions, not inferred from weekdays. Early closes are retained.
The source is the versioned third-party calendar, not an independent exchange
certification. Exceptional closure corrections require versioned maintenance.

Dependency is declared in requirements-scanner-history.txt. Install alongside
the project's existing requirements; it is not a replacement environment file.

## History adapter

`YahooHistorySnapshotProvider.fetch(listing, mapping, start=..., end=...,
timezone=...)` requests an inclusive local session-date interval. Yahoo's exclusive
end instant is the next local midnight, capped at the current acquisition time.
The S2.2C verifier uses the captured ticker response: one explicit daily history
call and its metadata feed both verification and snapshot construction.
Internal yfinance HTTP requests may exceed this count.

Arguments: interval=1d, auto_adjust=False, back_adjust=False, actions=False,
repair=False, keepna=True, raise_errors=True, configured history timeout.
No global yfinance exception configuration is changed. The timeout remains per
request, not a hard process deadline.

Daily OHLC, Close, Volume and Adj Close come from the same indexed response.
Close is not replaced by Adj Close, and a missing Adj Close stays unavailable.
The producer preserves Yahoo price/volume bases as supplied; this is not an
independent reconstruction of historical nominal prices across stock splits.
The turnover result remains a proxy subject to provider corporate-action quality.

The adapter validates timezone-aware daily timestamps, converts them to the
calendar timezone, and requires unique local-midnight session labels. It rejects
ambiguous timestamps, duplicates and out-of-request rows rather than dropping
them. Returned rows with missing observations remain in the snapshot; quality
gates decide their treatment. Snapshot provenance records yfinance version,
symbol, request range and adjustment settings.

## Zero-volume ambiguity

Upstream yfinance history processing can replace missing Volume with zero even
when keepna=True. Therefore a zero in the returned frame cannot by itself prove
zero trading. This adapter converts those volumes to None, retains their exact
dates in ambiguous_zero_volume_sessions, and reports ZERO_VOLUME_AMBIGUOUS.
It never forwards them as proven zero-volume days. Genuine low positive volumes
remain measurable; explicit negative/infinite values remain quality failures.
The pure core continues to accept proven zero volumes from a trustworthy producer.

Source implementation inspected for this behavior and request flags:
https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py
Calendar API and release:
https://pypi.org/project/exchange_calendars/4.13.2/

## Failure semantics

Transport errors retain S2.2C availability diagnostics and produce no synthetic
empty snapshot. Empty history is NO_DATA. Missing metadata/currency or malformed
daily structure prevents snapshot construction. A source identity mismatch can
retain the snapshot for audit but is blocked by the core. Nothing infers an IPO
from the first bar, Yahoo first-trade metadata or a short response.

## EUR pilot and persistence

Run `python -m tools.live_scanner_history_quality --input-report <S2.2B report>`.
Defaults select one eligible, globally collision-checked listing each for BIT and
XETRA, preferring A2A and SAP. Source currency must be EUR before requests start.
The first pilot accepts only these two venues; no artificial FX rates are supplied.
It requests 365 calendar days, processes sequentially with a pause and stops on
rate limit. Pending daily bars remain in the saved snapshot and are excluded by
the core according to close plus the one-hour publication grace.

The atomic JSON report in data/cache/scanner/history_audit includes full source
eligibility records, acquisitions (snapshots and verification evidence), calendar
contexts, evaluation times, gate decisions and fingerprints. Thus observed data
are retained for subsequent audit. This is write-through audit persistence, not
a history cache with expiry/reuse/resume. Do not confuse it with S2.2C cache-only.

Each result is checkpointed; interrupted/failed runs retain completed records.
Exit 0 means all selected listings took STANDARD; exit 2 means a completed partial
or rate-limit outcome, 1 execution/input failure, 130 user interruption.
BLOCKED/REVIEW_REQUIRED are legitimate measured outcomes, not automatically bugs.
The report does not generate opportunities or modify a portfolio.

## Validation

24 new offline tests passed (19 adapter tests and 5 pilot tests); 127 tests passed
in the available local S2.2C/S2.2D subset. Calendar tests use injected schedules;
history tests use injected ticker objects. No live Yahoo call or real local
exchange-calendars execution is claimed by these test counts.

Tests verify explicit bindings/version checks, holiday/early-close preservation,
truncated-calendar refusal, UTC/local date conversion, row alignment, missing
adjusted values, zero-volume ambiguity, error separation, single explicit history
acquisition, full report persistence and stop-on-rate-limit before another listing.
User Windows tests and the bounded EUR live pilot are the next acceptance steps.
