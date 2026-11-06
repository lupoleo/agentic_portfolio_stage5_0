# AI-8C.3-R2.1 — Company-Frame Scoring for Bipolar Components

| Field | Value |
| --- | --- |
| Type | Revision of a reopened contract (AI-8C.3-R2, not yet fully accepted) |
| Reopened contract | AI-8C.3 — Opportunity Scoring |
| Also affected | S2.F materialization gate (accepted directional policies) |
| Branch | `e2e-s4.0a-validation-harness` (separate commit) |
| Status | SAFETY CRITERIA MET — company-frame consistency NOT met (see §7); SHORT materialization suspended |
| Approved by | Operator, 2026-09-30 |

## 1. Reason

The R2 live acceptance (four LONG/SHORT pairs, 2026-09-30) showed that the
local model follows the direction for THESIS but not for FUNDAMENTAL: it kept
the company frame on both sides (UCG 60/60, BAS 62.5/60) and gave MIRM, with
37.9% revenue growth, 72.5 as support for a SHORT. Asking a small model to
invert a bipolar business assessment is unreliable; asking it to assess the
company is what it already does well.

## 2. Change

1. FUNDAMENTAL and EXPECTATIONS are always scored from the company's point of
   view, for LONG and SHORT alike. The prompt and both repair prompts say so
   explicitly and tell the model not to invert them.
2. Software mirrors them for SHORT around the neutral anchor
   (`score_short = 100 - score_company`), after the existing semantic
   calibration. The mirror commutes with that calibration on the 2.5 grid.
   Null scores stay null; rationales and evidence references are unchanged.
3. THESIS and CATALYST remain directional (model-scored for the direction);
   TECHNICAL remains deterministically mirrored (R2).
4. Diagnostics record the company-frame values and the mirrored components.
   Policy `ai-8c3-directional-scoring-v2`; prompt
   `opportunity-scoring-v14-directional-company-frame`.
5. Materialization accepts only scores produced by an accepted directional
   policy (`ai-8c3-directional-scoring-v2`). v1 scores, where the model
   inverted fundamentals itself, now fail closed with
   `SCORE_DIRECTION_MISMATCH`.

LONG scoring is numerically unchanged except through prompt wording.
Weights, calculator, confidence, and the 60 / 0.40 thresholds are unchanged.

## 3. Offline verification

Historical pairs (74, direction-blind, therefore company-frame on every
component), calculator verified to reproduce each persisted raw score:

| Variant | Both sides raw ≥ 60 | SHORT raw ≥ 60 | SHORT ≥ 60 with strong fundamentals (≥ 65) | Median LONG/SHORT gap |
| --- | ---: | ---: | ---: | ---: |
| Before | 14 | 18 | 2 | 2.86 |
| R2 (technical mirror) | 0 | 6 | 2 | 8.41 |
| R2.1 (technical, fundamental, expectations mirror) | 0 | 4 | 0 | 8.79 |

The operator's R2 live run, recomputed with the LONG-side (company-frame)
fundamental and expectations mirrored for SHORT and all other components as
observed:

| Listing | LONG raw | SHORT raw R2 | SHORT raw R2.1 |
| --- | ---: | ---: | ---: |
| BAMI.MI | 43.6 | 62.4 | 62.9 |
| BAS.DE | 52.9 | 71.3 | 61.5 |
| MIRM | 46.8 | 66.6 | 62.1 |
| UCG.MI | 55.1 | 53.3 | 49.3 |

MIRM and BAS lose the inflation from misread fundamentals and expectations;
BAMI, whose company-frame fundamentals are weak, keeps its SHORT support.

## 4. Residual risk

THESIS and CATALYST still depend on the model honouring the direction. THESIS
did so in the R2 run; CATALYST was mostly unchanged across directions.

## 5. Tests

`tests/test_ai_directional_scoring.py` adds: policy v2, mirror for SHORT only
with LONG + SHORT = 100 on both components, directional components not
mirrored, strong company fundamentals counting against a SHORT, null
preservation, prompt wording, and rejection of v1 scores at materialization.
The harness checks the accepted policy and shows company-frame values per pair.
Regression: 1,661 tests and 162 subtests passed.

## 6. Acceptance criteria

1. Offline regression green on Windows and Linux (CI); Replay green.
2. One fresh LIVE session where every directional score uses
   `ai-8c3-directional-scoring-v2` with source `HYPOTHESIS`, no pair scores
   raw ≥ 60 on both sides, and side effects are zero.
3. For each pair, the company-frame fundamental and expectations values of the
   LONG and SHORT sides are close (same company, same evidence), and the
   SHORT final values are their mirror. Operator review of the pair table.

## 7. Acceptance evidence (2026-09-30)

Validation run on the operator machine, commit `3790ea4` (tree `31522721`),
two LIVE waves (NYSE:HPE, XETRA:FOO, BIT:BAMI, NYSE:GFI), 1,778 s.
Regression 1,660 passed + 1 POSIX-only skip; Replay PASS; zero side effects.
All eleven directional scores used `ai-8c3-directional-scoring-v2` with source
`HYPOTHESIS`; no pair scored raw ≥ 60 on both sides.

Criterion 3 failed. Company-frame FUNDAMENTAL values of the same listing:

| Listing | LONG side | SHORT side | Final SHORT | Expected SHORT |
| --- | ---: | ---: | ---: | ---: |
| BAMI.MI | 42.5 | 65 | 35 | ~57.5 |
| BAMI.MI (wave 2) | 37.5 | 60 | 40 | ~62.5 |
| HPE | 62.5 | 47.5 | 52.5 | ~37.5 |
| GFI | 62.5 | 70 | 30 | ~37.5 |
| FOO.DE | 60 | 72.5 | 27.5 | ~40 |

Gaps reach 22.5 points in both directions: on BAMI the model still inverted
for the SHORT despite the instruction (double inversion penalised a valid
SHORT); on HPE the SHORT fundamental was inflated by 15 points. Causes: the
direction in the prompt contaminates the company-frame judgement, and each
hypothesis has its own research text. Instructions alone cannot fix this.

Decision (operator, 2026-09-30): SHORT materialization is suspended by a
code-level switch (`SHORT_MATERIALIZATION_ENABLED = False`, outcome
`SHORT_MATERIALIZATION_SUSPENDED`, replenishable). SHORT hypotheses are still
researched and scored for measurement. The fix, AI-8C.3-R2.2, is one shared,
direction-free company assessment per listing used by both hypotheses. LONG is
unaffected: its company frame and directional frame coincide and no mirror is
applied.

Same run: HPE `NEW_LONG` scored confidence-adjusted 64.7 (above 60) with HIGH
evidence quality and research confidence 0.85, and was excluded only as
`RESEARCH_NOT_COMPLETE`; its single material unknown ("long-term impact of new
contracts on margins") is a forward uncertainty. Four research listed
volatility as unknown although the evidence stated it, echoing an example in
the research prompt; both points are addressed by AI-8C.2-R1.
