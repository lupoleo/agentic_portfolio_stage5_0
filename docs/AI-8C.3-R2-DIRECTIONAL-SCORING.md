# AI-8C.3-R2 — Directional Opportunity Scoring

| Field | Value |
| --- | --- |
| Type | Reopening of a frozen contract |
| Reopened contract | AI-8C.3 — Opportunity Scoring |
| Also affected | S2.F scanner research integration (materialization gate, outcome reason) |
| Branch | `e2e-s4.0a-validation-harness` (separate commit) |
| Status | SAFETY CRITERIA ACCEPTED 2026-09-30 — semantic direction partially honoured (see §7) |
| Approved by | Operator, 2026-09-30 |

## 1. Reason

Opportunity Scoring did not receive the hypothesis direction. Its prompt, its
deterministic technical base and its factor extraction all measured support
for a LONG position. Direction was applied only after the eligibility gate,
when a `NEW_SHORT` opportunity took `bear_case` as its thesis.

Evidence from the operator state database (74 persisted `NEW_LONG`/`NEW_SHORT`
pairs on the same listing and integration run):

| Measure | Value |
| --- | ---: |
| Pairs with identical technical score | 63 of 74 |
| Median absolute raw-score difference LONG vs SHORT | 2.86 |
| `NEW_SHORT` with raw score ≥ 60 | 18 |
| Pairs where both LONG and SHORT score raw ≥ 60 | 14 |

A `NEW_SHORT` on a stock the system rated bullish could therefore pass the
score gate. The defect was masked only because research almost never reached
`COMPLETE`, so it had to be fixed before any change that unblocks research.

## 2. Change

1. `OpportunityScoringService.score()` accepts `direction`. The scanner
   integration passes `LONG` for `NEW_LONG` and `SHORT` for `NEW_SHORT`.
   Callers that pass nothing keep the historical LONG semantics, recorded as
   `direction_source = DEFAULT_LONG`.
2. Deterministic technical base: the directional contribution (momentum,
   price versus averages, elevated volume with bias) is mirrored for SHORT; the
   RSI-extreme penalty stays a risk in both directions. LONG results are
   identical to the previous mapping for every feature combination (tested
   exhaustively).
3. The scoring prompt states `HYPOTHESIS DIRECTION` and, for SHORT, the
   directional anchors for thesis, catalyst, fundamental, technical and
   expectations. Both repair prompts carry the direction line.
4. Factor extraction mirrors for SHORT (supporting: `bear_case`, `key_risks`;
   adverse: `bull_case`). Score/rationale consistency diagnostics invert
   polarity for SHORT.
5. Diagnostics persisted in `OpportunityScore.metadata.scoring_diagnostics`
   gain a `direction` block: policy `ai-8c3-directional-scoring-v1`, direction,
   source, LONG-frame and directional technical base, the model's technical
   opinion, and `possible_direction_ignored` when the opinion disagrees with
   the directional base by 20 points or more.
6. Fail-closed materialization gate: a directional hypothesis materializes only
   if its score records `direction_source = HYPOTHESIS` with the same direction.
   Otherwise the outcome is the new reason `SCORE_DIRECTION_MISMATCH`
   (replenishable). This rejects legacy direction-blind scores on resume.
7. Default `prompt_version`: `opportunity-scoring-v13-directional`.

Unchanged: score weights, calculator, confidence formula, the 60 / 0.40
materialization thresholds, research contracts, and all portfolio, risk and
execution contracts.

## 3. Impact analysis

- LONG scoring output changes only through the prompt text (direction line and
  rule); the deterministic LONG technical base is bit-identical.
- Existing `OpportunityScore` records are not rewritten. Their lack of a
  directional block makes them non-materializable under the new gate, which is
  the intended fail-closed behaviour.
- Run and session identities are unaffected: fingerprints do not include
  prompt versions.

Offline verification on the 74 historical pairs, applying only the
deterministic part (technical component mirrored, all other components as
persisted, calculator recomputed and first checked to reproduce every
persisted raw score exactly):

| Measure | Before | After (technical only) |
| --- | ---: | ---: |
| Pairs where both sides score raw ≥ 60 | 14 | 0 |
| `NEW_SHORT` with raw score ≥ 60 | 18 | 6 |
| Median absolute raw-score difference | 2.86 | 8.41 |

The non-technical components (thesis, catalyst, fundamental, expectations)
depend on the model honouring the direction; they can only be verified live.

## 4. Residual risk

A small local model may ignore the direction for the semantic components. The
technical component is protected by construction (deterministic base, bounded
±10 adjustment, `possible_direction_ignored` flag). The semantic components
are not. The acceptance run measures this directly on LONG/SHORT pairs.

Separate finding, not addressed here: both repair prompts are context-free
(they list evidence aliases but not the research content), so a repaired
component is scored with little information.

## 5. Tests

`tests/test_ai_directional_scoring.py`: exhaustive LONG parity with the
pre-R2 mapping, SHORT mirror with RSI risk preserved, prompt/metadata/diagnostics,
default-LONG provenance, bounded and flagged direction disagreement, factor
mirror, polarity inversion, repair prompts, SHORT prompt size guard, the
hypothesis-direction mapping and the materialization gate (missing, opposite,
default and matching direction). Harness inspection gains R2 checks.
Existing fakes and fixtures now declare direction as the real service does.
Regression: 1,653 tests and 162 subtests passed.

## 6. Acceptance criteria

1. Offline regression green on Windows and Linux (CI).
2. Validation harness `Replay` green.
3. One fresh LIVE replenishment session in which:
   - every new directional score records its hypothesis direction with
     source `HYPOTHESIS`;
   - no LONG/SHORT pair on the same listing has both raw scores ≥ 60;
   - zero broker orders, portfolio mutations and automatic executions.
4. Operator review of the per-component pair table in `summary.md`, to judge
   whether the model honours direction for the semantic components.

## 7. Acceptance evidence (2026-09-30)

Validation run on the operator machine, commit `761cf82` (tree `5ea3d1d4`),
two LIVE waves (BIT:BAMI, BIT:UCG, NASDAQ:MIRM, XETRA:BAS), 1,258 s:

| Criterion | Result |
| --- | --- |
| Offline regression | PASS: 1,652 passed, 1 skipped (POSIX-only), 162 subtests |
| Replay | PASS |
| Directional scores record their hypothesis direction | PASS: 8 of 8, source `HYPOTHESIS` |
| No LONG/SHORT pair with both raw scores ≥ 60 | PASS: 0 of 4 |
| Side effects | 0 broker orders, 0 portfolio mutations, 0 automatic executions |

Per-pair scores (thesis / catalyst / fundamental / technical / expectations):

| Listing | LONG raw | SHORT raw | LONG components | SHORT components |
| --- | ---: | ---: | --- | --- |
| BAMI.MI | 43.6 | 62.4 | 45 / 47.5 / 35 / 40 / 50 | 80 / 47.5 / 62.5 / 70 / 50 |
| BAS.DE | 52.9 | 71.3 | 65 / 57.5 / 62.5 / 15 / 50 | 70 / 65 / 60 / 85 / 85 |
| MIRM | 46.8 | 66.6 | 50 / 55 / 50 / 20 / 50 | 70 / 57.5 / 72.5 / 85 / 50 |
| UCG.MI | 55.1 | 53.3 | 55 / 57.5 / 60 / 50 / 50 | 55 / 50 / 60 / 50 / 50 |

Assessment:

- Technical: mirrored as designed on all four pairs; no
  `possible_direction_ignored` flag.
- Thesis: follows the direction (SHORT higher where technicals are bearish).
- Fundamental: not reliably directional. The model scored fundamentals in the
  company frame for both hypotheses (UCG 60/60, BAS 62.5/60) and gave MIRM,
  with 37.9% year-on-year revenue growth, 72.5 as support for a SHORT.
- Expectations and catalyst: inconsistent, mostly unchanged.

First live `COMPLETE` research and first `SCORED` score of the system:
BAMI.MI `NEW_SHORT` (research confidence 0.85, raw 62.4, confidence-adjusted
56.6), excluded as `SCORE_BELOW_THRESHOLD`.

Conclusion: R2 removes the contradictory-pair defect and is safe to publish.
The semantic components with a bipolar company-level scale (fundamental,
expectations) should be scored in the company frame and mirrored in software
for SHORT, as the technical component already is. This is proposed as
AI-8C.3-R2.1 and must precede any change that unblocks research completeness.

Harness note: the R1 check first flagged BAMI.MI `NEW_LONG` for the unknown
"Volatility persistence beyond current 22.37% level". It quotes the supplied
value, so it is a forward uncertainty, not a missing metric. The inspection now
counts only volatility unknowns without a quoted value; R1 passes on this run.
