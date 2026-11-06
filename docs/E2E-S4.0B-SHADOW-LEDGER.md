# E2E-S4.0B — Shadow Ledger

| Field | Value |
| --- | --- |
| Type | New measurement component (no business contract change) |
| Branch | `e2e-s4.0a-validation-harness` (separate commit) |
| Storage | `data/state/shadow_ledger.db` (git-ignored, separate from the state DB) |
| Entry points | `python -m tools.shadow_ledger export / measure / report / list`; automatic export after each harness `Live` level |
| Status | IMPLEMENTED |
| Approved by | Operator, 2026-10-01 (horizons 5/10/20 sessions; listing's market index as benchmark) |

## 1. Reason

Since the first `COMPLETE` research, every scored directional hypothesis has
stopped below the 60 confidence-adjusted threshold (49–59). The threshold was
set without outcome data. Changing it requires evidence of whether higher
scores lead to better outcomes, not an opinion.

## 2. What is recorded

Every directional hypothesis (`NEW_LONG`, `NEW_SHORT`) with a
confidence-adjusted score, whatever its research status and whether or not it
passed the gate: ticker, direction, evaluation time, raw and adjusted score,
the five components, score and research confidence, evidence quality, research
status and contract, directional policy, outcome status and reason. Entries are
keyed by `opportunity_score_id` and inserted once (idempotent).

## 3. Measurement

`measure` fetches daily adjusted closes from Yahoo and, for 5, 10 and 20
sessions:

- reference = the last close strictly before the evaluation day (no
  look-ahead); horizon = the close that many sessions later;
- directional return = return × (+1 LONG, −1 SHORT);
- directional excess return = directional (stock return − index return), with
  the index of the listing's market (FTSE MIB, DAX, CAC 40, AEX, IBEX,
  FTSE 100, SMI, …; S&P 500 for US listings; none for unmapped markets);
- horizons not yet reached are left pending and measured on a later call;
  provider failures are counted, never raised.

## 4. Report

`report` groups measured entries by adjusted score (<50, 50–55, 55–60, ≥60) per
horizon: n, hit rate, mean and median directional return, excess return, and
Spearman correlation between adjusted score and (excess) return; also LONG
versus SHORT. By default only the current directional policy
(`ai-8c3-directional-scoring-v3`) is included; `--all-policies` adds earlier,
direction-blind scores as a baseline; `--complete-only` restricts to
`COMPLETE` research.

## 4b. Listing

`list` prints one line per recorded hypothesis: evaluation time, ticker,
direction, adjusted and raw score, research status, outcome reason and the
directional return at 5, 10 and 20 sessions (`-` while pending). Filters:
`--ticker`, `--direction LONG|SHORT`, `--complete-only`, `--current-policy`.
`--csv PATH` also writes all columns, including index-relative returns, for
Excel (`;` separator and decimal comma by default; `--decimal-point` for
`,` and `.`).

## 5. Safety

Source databases are opened read-only (checked by test). The ledger is a
separate file; no portfolio, proposal, opportunity, risk or execution state is
read for decisions or written. The harness export never fails the `Live` level.

## 6. Limits

About eight scored hypotheses per validation run. After a month of daily runs
the sample is 150–200 entries: enough to see whether the score has any
predictive power, not for fine calibration. Buckets with fewer than 30
measurements are not evidence for a threshold change; the report says so.
Returns ignore costs, borrow fees for SHORT and position sizing.

## 7. Tests

`tests/test_e2e_shadow_ledger.py`: benchmark mapping, selection of scored
directional hypotheses, idempotent export with an unchanged source, reference
and horizon dates, LONG/SHORT sign, excess return, pending horizons, provider
failure, Spearman, report buckets and filters, Markdown, CLI round trip.
Regression: 1,767 tests and 162 subtests passed.
