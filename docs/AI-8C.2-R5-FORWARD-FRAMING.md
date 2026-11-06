# AI-8C.2-R5 — Forward-Framed Unknowns and Status Reassessment

| Field | Value |
| --- | --- |
| Type | Revision of a reopened contract (AI-8C.2 research validator and repair) |
| Branch | `ai-8c2-r5-forward-framing` |
| Status | ACCEPTED — live evidence 2026-10-05/06 and 2026-10-08 (reassessment not yet observed live) |
| Approved by | Operator, 2026-10-05 (relaxation: future outcomes filed as unknowns no longer block `COMPLETE`) |

## 1. Reason

With news restored (AI-8C.2-R4), the four research of the 2026-10-05 live run
(ENEL.MI, LDO.MI) were all `PARTIAL`. The remaining blocking items were mostly
future outcomes the model filed as unknowns ("Impact of M&A activity on
long-term margins", "Exact future earnings trajectory") or forward items the
guard reclassified as gaps ("Market acceptance of new valuation metrics",
"Volatility from macroeconomic shifts"), plus one sector comparison phrased
without a recognised marker ("Sector-specific pricing multiples not
disclosed").

`PARTIAL` is chosen by the model itself and software never promotes it.
Making these items non-blocking alone would change nothing; the model must
also be asked to reassess.

## 2. Change

1. **Forward-framed unknowns.** An unknown with explicit forward framing
   ("impact of", "future", "sustainability", "trajectory", "outlook",
   "market reaction/acceptance", "potential", "will/could/may", …) and neither a
   missing-data marker ("not provided", "not disclosed", "missing", …) nor
   as-of wording ("latest", "recent", "current", "reported", "historical",
   "consensus", "guidance") is treated as a forward uncertainty: recorded in
   `metadata.forward_framed_unknowns`, not a material gap.
2. Forward markers gain "acceptance", "macroeconomic", "shifts", "pending", so
   such items in `forward_uncertainties` are no longer reclassified as gaps.
3. "Sector-specific" joins the comparison markers of the AI-8C.2-R2
   context-gap rule (a refinement of the already approved principle).
4. **Reassessment.** A `PARTIAL` research with MEDIUM or HIGH evidence quality,
   no material gap after all rules and at least one future outcome (in either
   list) raises the warning `FORWARD_ITEMS_IN_UNKNOWNS`. It triggers one
   field-scoped repair of `unknowns`, `forward_uncertainties`,
   `research_status` and `requires_additional_research`, asking the model to
   move future outcomes to `forward_uncertainties` and to reassess the status
   under rules U4, 16 and 17. The model decides; if it keeps `PARTIAL`, the
   research is kept as is.
5. Metadata records `initial_research_status`,
   `forward_reclassification_requested` and `forward_framed_unknowns`.
   Contract `ai-8c2-research-v4-forward-framing`; the research prompt is
   unchanged.

Unchanged: material terms, missing-data markers, status and quality rules,
scoring, thresholds (60 / 0.40). Software never promotes research to
`COMPLETE`.

## 3. Offline estimate

On the four research of the 2026-10-05 run (ENEL.MI and LDO.MI, LONG and
SHORT), three would have no blocking gap and receive the reassessment; ENEL.MI
`NEW_LONG` keeps one genuine gap ("Full details on balance sheet leverage and
debt structure").

## 4. Residual risk

The framing and as-of lists are heuristics; an as-of gap phrased as a future
impact could pass as forward. The reassessment adds one LLM call to eligible
research. More `COMPLETE` research will reach the unchanged score gate; the
shadow ledger measures whether that matters.

## 5. Tests

`tests/test_ai_research_forward_framing.py` (18 tests): live examples
non-blocking, as-of and missing-data items still blocking, forward items no
longer reclassified, sector-specific multiples as context gap, warning only
without remaining gaps and with MEDIUM/HIGH quality, reassessment when forward
items sit only in their own list, field-scoped repair leading to `COMPLETE`,
and no promotion when the model keeps `PARTIAL`. Regression: 1,816 tests and
162 subtests passed.

## 6. Acceptance criteria

1. Offline regression green (CI).
2. One LIVE replenishment in which eligible research receive the reassessment
   (`forward_reclassification_requested`), no `COMPLETE` research contains a
   material gap, all checks pass and side effects are zero.
3. Reported, not required: the number of `COMPLETE` research and any
   opportunity.

## 7. Acceptance evidence

Accepted on 2026-10-08 on its safety criteria. CI `offline-regression` green.

**R5 sessions of 2026-10-05 and 2026-10-06** (production database, portfolio
snapshot of 2026-10-04; 20 research):

| Status chosen by the model | Final status | Research |
| --- | --- | ---: |
| `COMPLETE` | `COMPLETE` | 11 |
| `COMPLETE` | `PARTIAL` (genuine gap kept, e.g. "Impact of recent acquisitions on margins") | 6 |
| `COMPLETE` | `INSUFFICIENT_EVIDENCE` | 1 |
| `PARTIAL` | `PARTIAL` (blocking gaps: 2BTC.DE without fundamentals; ENEL.MI sector multiples) | 2 |

**Session of 2026-10-08** (new portfolio snapshot `SNAP-20261008-150533-62ea3a`,
root `s4a-74e012e841ae996daea492b3`, replenishment
`s4a1-83718ca52138c4f42aa7a5aa`, waves NASDAQ:DRH + NASDAQ:TLRY and
NASDAQ:CRUS + NYSE:FCPT): 12 hypotheses, 11 research, 5 `COMPLETE`; inspection
verdict PASS (R1, R2 and research-contract checks); every new research uses
`ai-8c2-research-v4-forward-framing`; no `COMPLETE` research contains a
material gap; zero broker orders, portfolio mutations and automatic
executions. Highest confidence-adjusted score so far: SAP.DE `NEW_LONG` 59.77
(raw 66.75, score confidence 0.58), below the unchanged threshold of 60.

| Criterion | Result |
| --- | --- |
| Offline regression | PASS (CI) |
| No `COMPLETE` research with a material gap | PASS (16 of 16 across both periods) |
| All inspection checks, zero side effects | PASS |
| Eligible research receive the reassessment | NOT OBSERVED: no `PARTIAL` research was left with only future outcomes |

The deterministic part works: future outcomes no longer block research the
model judges complete, and genuine gaps still demote. The reassessment is
covered offline but has not yet fired live; with news available (R4) the
model already chooses `COMPLETE` when evidence suffices. If it keeps not
firing it may be removed for simplicity.

Finding for follow-up (AI-8C.2-R6 candidate): on 2026-10-08 both A2A.MI
research were `PARTIAL` with HIGH quality and no gap, unknown or future
outcome at all. `NEW_LONG` declared no further research needed and failed
`PARTIAL_WITHOUT_MORE_RESEARCH` after its repair (`PROCESSING_FAILED`);
`NEW_SHORT` declared further research needed without naming it and was kept
as `PARTIAL`. Neither is eligible for the R5 reassessment, which requires a
future outcome.
