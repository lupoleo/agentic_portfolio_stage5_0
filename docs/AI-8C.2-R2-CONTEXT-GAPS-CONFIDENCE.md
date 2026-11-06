# AI-8C.2-R2 — Context Gaps, Supplied Facts, Research Confidence, Bank Fundamentals

| Field | Value |
| --- | --- |
| Type | Revision of a reopened contract (AI-8C.2) including an operator policy decision |
| Reopened contract | AI-8C.2 — Opportunity Research and evidence (validator, prompt, Yahoo fundamental evidence) |
| Branch | `e2e-s4.0a-validation-harness` (separate commit) |
| Status | ACCEPTED 2026-09-30 — first COMPLETE research and first SCORED LONG (see §7) |
| Approved by | Operator, 2026-09-30 (including the context-gap policy) |

## 1. Reason

The AI-8C.2-R1 live run (2026-09-30) produced no `COMPLETE` research. The
inspection of its evidence bundles showed that fundamental and analyst
evidence was present in every bundle, and classified the 34 blocking gaps:

| Category | Items |
| --- | ---: |
| Peer / sector valuation (no provider) | 11 |
| Forward-looking items still filed as unknowns | 7 |
| Latest-quarter margins (trailing margins supplied) | 6 |
| Facts the evidence already supplies (consensus, operating margin, P/E) | 5 |
| Company guidance (not available from Yahoo) | 3 |

Peer valuation alone blocked 9 of 10 research. In addition,
`research_confidence` was 0.0 in 5 of 10 research (undefined in the prompt),
forcing the confidence-adjusted score to 50, and banks received industrial
metrics (gross margin 0%, operating cash flow −7.3 bn EUR) that are not
meaningful for them.

## 2. Change

1. **Context-gap policy (operator decision).** A comparison or finer
   granularity of a measure the evidence supplies is recorded as a *context
   gap*: kept in `unknowns`, stored in `metadata.context_gaps`, but excluded
   from the material gaps that block `COMPLETE`. Each rule requires its anchor
   in the evidence:
   - peer, sector or relative comparisons (valuation needs a P/E multiple or
     an analyst price target; other comparisons need the fundamental snapshot);
   - company guidance when analyst earnings/revenue estimates are supplied;
   - quarter, trend, breakdown or segment detail of margins, revenue, EPS or
     cash flow when the trailing measure is supplied.
   Without the anchor the item remains blocking exactly as before. Without
   evidence (for example when an older record is re-read) nothing is exempted.
2. **Supplied fundamental and analyst facts.** An unknown claiming consensus
   or EPS estimates, price targets, P/E, operating or profit margin, operating
   or free cash flow, or total debt is missing now contradicts the evidence
   when it is supplied (`UNKNOWN_CONTRADICTS_SUPPLIED_FACT`), unless it asks
   for a refinement (peer, sector, quarter, trend, growth, revision, guidance,
   P/B, history) or carries forward framing. The existing repair and
   deterministic canonicalization remove it.
3. **Research confidence.** The prompt defines `research_confidence` with
   anchors (0.2 / 0.5 / 0.7 / 0.85). A confidence below 0.2 with MEDIUM or HIGH
   evidence quality and a non-`INSUFFICIENT_EVIDENCE` status raises the warning
   `RESEARCH_CONFIDENCE_INCOHERENT`, which triggers a field-scoped repair of
   `research_confidence` only. If the model repeats the value, the research is
   kept as is (fail-closed through the score). Metadata records the initial
   value and whether it was repaired.
4. **Bank fundamentals.** For Yahoo sector "Financial Services" with a bank or
   insurance industry, the fundamental evidence omits gross margin, operating
   and free cash flow, liquidity ratios and EV/EBITDA, and states why.

Versions: prompt `opportunity-research-v1.5-context-gaps-confidence`,
contract `ai-8c2-research-v3-context-gaps`, fundamental evidence
`yahoo-fundamental-evidence-v3-financials-aware`.

Unchanged: the material-term list, status and quality rules, the scoring
weights and thresholds (60 / 0.40), and SHORT materialization (suspended).
Software never promotes research to `COMPLETE`.

## 3. Offline estimate on the R1 live run

Applying the new validator rules to the ten research of that run, with the
evidence each one actually received:

- 3 unknowns removed as contradicting supplied facts;
- 21 items become context gaps;
- research without any blocking gap: 0 → 3 of 10.

The remaining blockers are mostly forward-looking items the model still files
as unknowns ("sustainability of free cash flow growth"); they stay blocking.

## 4. Residual risk

The context-gap policy makes `COMPLETE` less strict on comparisons. It relies
on marker lists and on the anchors being present in the evidence text. The
confidence repair relies on the model; a repeated 0.0 still suppresses the
score. SHORT remains suspended pending AI-8C.3-R2.2.

## 5. Tests

`tests/test_ai_research_context_gaps.py` (33 tests): versions, context gaps
with and without anchors, non-comparison gaps, no exemption without evidence,
supplied-fact contradictions and their refinement/forward exemptions, the
confidence warning and its field-scoped repair (successful and repeated),
metadata, prompt wording, and bank versus non-bank fundamentals. Regression:
1,723 tests and 162 subtests passed.

## 6. Acceptance criteria

1. Offline regression green (CI) and Replay green.
2. One fresh LIVE session in which every research records
   `ai-8c2-research-v3-context-gaps`, no `COMPLETE` research contains a
   material gap, no MEDIUM/HIGH research keeps a confidence below 0.2, and
   side effects are zero.
3. Operator review of blocking gaps, context gaps and confidence repairs per
   research in `summary.md`.
4. Reported, not required: `COMPLETE` research and any LONG opportunity.

## 7. Acceptance evidence (2026-09-30)

Validation run on the operator machine, commit `be4b31e` (tree `007134ea`),
two LIVE waves (NASDAQ:WBD, XETRA:BMW, BIT:BAMI, BIT:UCG), 1,164 s.
Regression 1,722 passed + 1 POSIX-only skip; Replay PASS; zero side effects;
no history failure or quarantine.

| Criterion | Result |
| --- | --- |
| Every research records `ai-8c2-research-v3-context-gaps` | PASS (8 of 8) |
| No `COMPLETE` research contains a material gap | PASS |
| No MEDIUM/HIGH research keeps confidence below 0.2 | PASS: confidence 0.5–0.7 in all 8; no repair needed |
| AI-8C.3-R1 / R2 checks | PASS |

Outcome: the first two live `COMPLETE` research of the system.

- WBD `NEW_LONG`: `COMPLETE`, all five components scored, raw 61.75,
  confidence-adjusted 55.14, excluded as `SCORE_BELOW_THRESHOLD` (60). Score
  confidence 0.4375 = research confidence 0.70 × MEDIUM quality factor 0.75 ×
  coverage 0.833 × completeness 1.0.
- UCG.MI `NEW_SHORT`: `COMPLETE`, confidence-adjusted 50.56, excluded as
  `SHORT_MATERIALIZATION_SUSPENDED`.

Context gaps worked as designed (peer valuation no longer blocked WBD or
UCG). One comparison phrased as "Comparative sector P/E ratios not quantified"
still blocked UCG `NEW_LONG`; "comparative" is added to the comparison markers
in AI-8C.2-R3. Evidence quality was MEDIUM in all eight research; the cause,
a uniform 90-day staleness rule applied to fundamentals dated by their last
reported quarter, is addressed by AI-8C.2-R3.
