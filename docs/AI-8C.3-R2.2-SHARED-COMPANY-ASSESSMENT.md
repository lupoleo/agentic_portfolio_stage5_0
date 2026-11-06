# AI-8C.3-R2.2 — Shared, Direction-Free Company Assessment

| Field | Value |
| --- | --- |
| Type | Revision of a reopened contract (AI-8C.3 Opportunity Scoring) |
| Also affected | Stage 4 runtime wiring; S2.F materialization gate for SHORT |
| Branch | `e2e-s4.0a-validation-harness` (separate commit) |
| Status | ACCEPTED 2026-09-30; SHORT materialization re-enabled by the operator |
| Approved by | Operator, 2026-09-30 |

## 1. Reason

After R2.1, FUNDAMENTAL and EXPECTATIONS were meant to be scored in the
company frame and mirrored for SHORT. Three live runs showed the company-frame
values of the same listing still differing between the LONG and SHORT sides by
up to 22.5 points, in both directions (BAMI fundamental 42.5 vs 65 and 50 vs
72.5; HPE 62.5 vs 47.5; NIO expectations 50 vs 65; BMW expectations 50 vs 78).
Two causes: the hypothesis direction in the scoring prompt contaminates the
company judgement, and each hypothesis has its own research text.

## 2. Change

1. New `CompanyAssessmentService` (`app/ai/company_assessment.py`): one
   direction-free LLM call per listing scores FUNDAMENTAL and EXPECTATIONS from
   the FUNDAMENTAL and ANALYST evidence only. The prompt contains no direction.
   Policy `ai-8c3-company-assessment-v1`, prompt
   `company-assessment-v1-direction-free`.
2. The assessment is cached by a fingerprint of the ticker and the
   content-addressed evidence IDs, so the LONG and SHORT hypotheses of a
   listing, which receive identical evidence, share one assessment and one
   call.
3. `OpportunityScoringService` (when constructed with the service, as the
   Stage 4 runtime and the live integration tool now do) replaces the scoring
   model's FUNDAMENTAL and EXPECTATIONS with the shared values, before the
   existing semantic calibration and the SHORT mirror. It does so only for
   SCORABLE components whose shared score cites evidence present in that
   research; otherwise the scoring model's value is kept and the source says
   so (`SCORING_MODEL`, `SCORING_MODEL_SHARED_NOT_GROUNDED`).
4. Fail-closed: no company evidence, a provider failure or an ungrounded shared
   score leave the scoring model's values in place; they never raise.
5. Diagnostics (persisted in the score) record the shared assessment, its
   fingerprint, the per-component source and the scoring model's own values.
   Directional policy becomes `ai-8c3-directional-scoring-v3`; materialization
   accepts only v3 scores.
6. When SHORT materialization is re-enabled, a SHORT score materializes only if
   every scored company-frame component came from the shared assessment
   (otherwise `SCORE_DIRECTION_MISMATCH`). SHORT stays suspended in this
   commit; re-enabling it is a separate operator decision after live
   acceptance.

Unchanged: the scoring prompt, weights, calculator, calibration, thresholds
and the LONG path except for the source of its company-frame values.

Cost: one additional LLM call per listing (not per hypothesis).

## 3. Residual risk

Two direction-free calls on the same evidence are still stochastic, so a
resumed run that loses the in-memory cache may compute a slightly different
assessment for the second hypothesis. The contamination by direction, the
dominant cause, is removed in every case.

## 4. Tests

`tests/test_ai_company_assessment.py` (11 tests): evidence selection and a
direction-free prompt, caching by evidence, no call without company evidence,
provider failure, ungrounded scores, LONG and SHORT sharing one assessment with
identical company frames and mirrored finals despite a self-contradicting
scoring model, not-grounded fallback, unscorable components, disabled service,
the SHORT gate and the runtime wiring. Harness: company-frame identity check
within pairs sharing an assessment. Regression: 1,742 tests and 162 subtests
passed.

## 5. Acceptance criteria

1. Offline regression green (CI) and Replay green.
2. One fresh LIVE session in which every directional score uses
   `ai-8c3-directional-scoring-v3`, company-frame values are identical within
   every LONG/SHORT pair sharing an assessment, and side effects are zero.
3. Operator decision on re-enabling SHORT materialization.

## 6. Acceptance evidence (2026-09-30)

Validation run on the operator machine, commit `d041fde` (tree `c093eebe`),
two LIVE waves (BIT:BAMI, BIT:UCG, BIT:TIT, NASDAQ:CZR), 1,139 s.
Regression 1,741 passed + 1 POSIX-only skip; Replay PASS; zero side effects;
no history failure.

| Criterion | Result |
| --- | --- |
| Every directional score uses `ai-8c3-directional-scoring-v3` | PASS (8 of 8) |
| Company-frame values identical within pairs sharing an assessment | PASS (4 of 4 pairs, one fingerprint per listing) |
| Company-frame components sourced from the shared assessment | 16 of 16 |
| No LONG/SHORT pair with both raw scores ≥ 60 | PASS |

| Listing | Shared fundamental / expectations | LONG final | SHORT final (mirror) |
| --- | --- | --- | --- |
| BAMI.MI | 50 / 50 | 50 / 50 | 50 / 50 |
| UCG.MI | 52.5 / 75 | 52.5 / 75 | 47.5 / 25 |
| CZR | 50 / 55 | 50 / 55 | 50 / 45 |
| TIT.MI | 52.5 / 55 | 52.5 / 55 | 47.5 / 45 |

In the previous three runs the same comparison diverged by up to 22.5 points.
CZR `NEW_LONG` was `COMPLETE` and `SCORED` at a confidence-adjusted 53.19,
excluded as `SCORE_BELOW_THRESHOLD`.

The run's R1 check reported one research (UCG.MI) listing
"Impact of recent volatility on long-term valuation". That is an implication,
not a missing metric, and the research validator correctly does not treat it
as a contradiction; the harness check is now aligned with the validator rule
and passes on this run.

## 7. SHORT materialization re-enabled (operator decision, 2026-09-30)

After the live acceptance above, the operator re-enabled SHORT
materialization (`SHORT_MATERIALIZATION_ENABLED = True`). A `NEW_SHORT`
materializes only with all of: `COMPLETE` research; a v3 directional score
computed for SHORT from the hypothesis; every scored company-frame component
taken from the shared company assessment; the unchanged 60 / 0.40 gates. The
switch remains in code; setting it to `False` suspends SHORT again with the
replenishable outcome `SHORT_MATERIALIZATION_SUSPENDED`.

## 8. Live run with SHORT re-enabled and three model-output fixes (2026-10-01)

Validation run `20261001T080054Z` (commit `f255b4e`), waves NASDAQ:XERS,
NYSE:ACA, NYSE:FMC, NYSE:PNC: all checks PASS, zero side effects, four
`COMPLETE` research (XERS LONG 55.1, PNC SHORT 53.4, FMC SHORT 52.6, FMC LONG
49.1, all `SCORE_BELOW_THRESHOLD`). SHORT hypotheses followed the same gates as
LONG; `SHORT_MATERIALIZATION_SUSPENDED` no longer appeared.

Three hypotheses failed with `PROCESSING_FAILED`, forcing wave retries and the
quarantine of FMC. The persisted diagnostics showed three model-output defects:

| Hypothesis | Defect | Fix |
| --- | --- | --- |
| XERS `NEW_SHORT` | the scoring model left EXPECTATIONS null and the targeted repair returned null again | the shared company assessment is now applied before the scorability check, so a grounded shared value fills the component and no repair is needed; without a shared value the repair runs as before |
| FMC `NEW_LONG` | `research_confidence` returned as 30 instead of 0.30; the whole research failed schema validation | a value in (1, 100] is read as a percentage and divided by 100; out-of-range values still fail |
| FMC `NEW_SHORT` | the technical component had no rationale and cited non-existent aliases | when the research cites the canonical TECHNICAL evidence, a missing rationale is replaced by a statement of the deterministic computation and invalid citations by that evidence (recorded in `technical_fallback`); without it the defect still fails closed |

None of the fixes changes a score: the technical value is computed by software,
the shared values were already used later in the pipeline, and the confidence
fix restores the value the model meant.

## 9. Live confirmation of the fixes (2026-10-01)

Validation run with commit `fbee600` (tree `0e2ea183`), waves BIT:BAMI,
NYSE:WTRG, BIT:TIT, BIT:UCG, 1,389 s (previous run 2,486 s): zero processing
failures, no wave retry, no quarantine; all checks PASS. Four of eight research
`COMPLETE` (WTRG SHORT 54.7, UCG LONG 52.8, TIT SHORT 52.8, TIT LONG 50.7), all
excluded as `SCORE_BELOW_THRESHOLD`; every company-frame component came from the
shared assessment.
