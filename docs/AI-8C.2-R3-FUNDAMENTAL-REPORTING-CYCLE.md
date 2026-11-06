# AI-8C.2-R3 — Fundamental Freshness Follows the Reporting Cycle

| Field | Value |
| --- | --- |
| Type | Revision of a reopened contract (AI-8C.2 evidence quality) |
| Changed | `EvidenceQualityEvaluator` freshness for FUNDAMENTAL evidence; research comparison markers |
| Branch | `e2e-s4.0a-validation-harness` (separate commit) |
| Status | ACCEPTED 2026-09-30 |
| Approved by | Operator, 2026-09-30 |

## 1. Reason

Fundamental evidence is dated by the end of the most recently reported
quarter (`mostRecentQuarter`). The evidence-quality policy applied one rule to
every kind: older than 90 days is stale, and any stale item makes HIGH quality
impossible. For a calendar-quarter reporter the last reported quarter ends on
30 June; on 30 September it is 92 days old although no newer quarter can exist
yet. From the end of each quarter until the next report, roughly one to six
weeks each quarter, almost every company was therefore capped at MEDIUM.

In the AI-8C.2-R2 live run all eight research were MEDIUM. WBD `NEW_LONG`
(`COMPLETE`, raw 61.75) received a MEDIUM quality factor of 0.75 in its score
confidence. HPE, whose fiscal quarter ended on 31 July, had been HIGH in an
earlier run.

## 2. Change

1. FUNDAMENTAL freshness follows the reporting cycle: up to 45 days after the
   period end counts as fully fresh (1.0), up to 135 days (one quarter plus a
   45-day publication window) as current (0.7), beyond that as stale (0.1)
   with the new warning `FUNDAMENTAL_REPORTING_PERIOD_STALE`.
2. All other evidence kinds keep the 90-day rule unchanged.
3. Policy identifiers: `evidence-quality-v2-reporting-cycle` and
   `evidence-quality-v2-semantic-coverage-reporting-cycle`; the window is
   recorded as `fundamental_reporting_window_days`.
4. Research comparison markers gain "comparative" (UCG `NEW_LONG` was blocked
   by "Comparative sector P/E ratios not quantified").
5. The harness inspection reports each bundle's fundamental period end and its
   age, to verify the diagnosis on live data.

Unchanged: the HIGH/MEDIUM/LOW thresholds, provenance and diversity scoring,
the single-source guard, the score calculator and the 60 / 0.40 gates.
Semi-annual reporters remain stale for part of the year, which is
conservative.

## 3. Expected effect

For WBD in the R2 run, HIGH quality would raise score confidence from 0.4375
to 0.583 and the confidence-adjusted score from 55.1 to about 56.9, still
below 60. The change removes a calendar artifact; it does not by itself create
opportunities. With typical factors a 60 adjusted score still requires a raw
score of roughly 67 or more.

## 4. Tests

`tests/test_ai_evidence_quality.py`: a 92-day-old quarter is not stale and
allows HIGH; 136 days is stale with the new warning; a recently reported
quarter scores fresher; other kinds keep the 90-day rule; the window is
recorded. The existing stale test now uses a 150-day fundamental.
Regression: 1,730 tests and 162 subtests passed.

## 5. Acceptance criteria

1. Offline regression green (CI) and Replay green.
2. One fresh LIVE session in which the inspection shows fundamental period ends
   and ages, research with fundamentals within 135 days are not demoted for
   staleness, and all AI-8C.2 and AI-8C.3 checks pass with zero side effects.

## 6. Acceptance evidence (2026-09-30)

Validation run on the operator machine, commit `72ac9d3` (tree `889990e2`),
two LIVE waves (NASDAQ:WBD, NYSE:NIO, BIT:BAMI, BIT:UCG), 1,115 s.
Regression 1,729 passed + 1 POSIX-only skip; Replay PASS; zero side effects;
all AI-8C.2 and AI-8C.3 checks PASS.

- Every bundle's fundamental period ended on 2026-06-30, 92.8 days before the
  run; no bundle carried a stale-period warning.
- All eight research were HIGH evidence quality (previous run: all MEDIUM).
- WBD `NEW_LONG` scored raw 65.4 and confidence-adjusted 58.97 (previous run
  55.14 at MEDIUM), but its research stayed `PARTIAL`: the blocking gap was
  "Netflix's revenue growth and subscriber metrics", data about another
  company named in the news.
- NIO `NEW_SHORT`: `COMPLETE`, confidence-adjusted 55.5, stopped by the SHORT
  suspension.
- Company-frame values still diverged within pairs (BAMI fundamental 50 vs
  72.5, NIO expectations 50 vs 65, WBD fundamental 52.5 vs 37.5), confirming
  the need for AI-8C.3-R2.2.
