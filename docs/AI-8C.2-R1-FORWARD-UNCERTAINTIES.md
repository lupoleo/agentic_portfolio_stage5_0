# AI-8C.2-R1 — Missing As-Of Facts versus Forward Uncertainties

| Field | Value |
| --- | --- |
| Type | Reopening of a frozen contract |
| Reopened contract | AI-8C.2 — Opportunity Research (prompt, model, coverage validator) |
| Also affected | AI-8C.3 scoring context and uncertainty factors (read-only consumer) |
| Branch | `e2e-s4.0a-validation-harness` (separate commit) |
| Status | ACCEPTED 2026-09-30 (contract criteria); no COMPLETE research yet — remaining gaps are genuine evidence gaps (see §7) |
| Approved by | Operator, 2026-09-30 |

## 1. Reason

`COMPLETE` research was almost unreachable. The model listed future outcomes
("long-term sustainability of valuation", "impact of the merger on future
earnings") as `unknowns`, and the coverage validator treats any unknown that
mentions earnings, revenue, margin, valuation, sector, peer or similar terms
as material. Such items are inherent to every investment: no evidence available
today can remove them.

Evidence:

- Across 191 persisted research records, 189 were `PARTIAL`.
- In the three live validation runs of 2026-09-30, most remaining material
  unknowns were forward-looking. HPE `NEW_LONG` (HIGH evidence quality,
  research confidence 0.85) scored 64.7 confidence-adjusted, above the 60
  threshold, and was excluded only because of "Long-term impact of new
  contracts on margins".
- Four research in the R2.1 run listed volatility as unknown although the
  evidence stated it, echoing an example in the prompt itself (rule 10:
  "technical volatility is unknown").

## 2. Change

1. `forward_uncertainties: list[str]` is added to the model output schema and
   to `OpportunityResearch` (default empty; legacy records load unchanged).
2. Prompt (`opportunity-research-v1.4-forward-uncertainties`):
   - `unknowns` are facts that exist or should be knowable at the evidence
     date but are not supplied, phrased as missing data;
   - `forward_uncertainties` are future outcomes no evidence available today
     can establish;
   - forward uncertainties never by themselves require additional research or
     prevent `COMPLETE`;
   - the volatility example is removed: supplied RSI, moving averages,
     relative volume or volatility must never be listed as unknown.
3. Coverage validator (deterministic guard against disguised gaps):
   material gaps = material `unknowns` (unchanged rule) plus every forward item
   that reads as missing as-of data (markers such as "not provided",
   "not disclosed", "missing", "unavailable", "not specified") or that contains
   a material term without any forward framing. `COMPLETE` with any material
   gap remains an error, and the existing repair and deterministic status
   normalization downgrade it to `PARTIAL`.
4. An unknown claiming volatility is missing now contradicts supplied
   canonical TECHNICAL evidence (`UNKNOWN_CONTRADICTS_SUPPLIED_FACT`), unless
   it quotes a value; the existing repair and deterministic canonicalization
   remove it.
5. Research metadata records `research_contract =
   ai-8c2-research-v2-forward-uncertainties`, `material_gaps`,
   `forward_items_reclassified_as_gaps` and `forward_uncertainty_count`.
6. Scoring reads forward uncertainties as context and as uncertainty factors.

Unchanged: the material-term list, every status and quality rule, repair
budgets, scoring weights and thresholds, and all portfolio, risk and execution
contracts. The software never promotes a `PARTIAL` research to `COMPLETE`;
only the model can, and only the validator can refuse it.

## 3. Impact and offline estimate

Existing research is not rewritten. Offline, splitting historical unknowns
into forward-looking and gap items with the same markers (an upper bound,
because only a live model decides status):

| Population | No material gap, quality ≥ MEDIUM: before | after |
| --- | ---: | ---: |
| State DB research since 2026-09-28 (151) | 9 | 26 |
| R2.1 live run research (11) | 0 | 4 (including HPE `NEW_LONG`) |

The research prompt template grows by about 870 characters (≈220 tokens).

## 4. Residual risk

The markers are heuristics. A forward item phrased with forward framing could
still hide a present gap; the model must also choose `COMPLETE` and clear
`requires_additional_research`. SHORT materialization remains suspended
(AI-8C.3-R2.2), so any new opportunity in the acceptance run can only be LONG.

## 5. Tests

`tests/test_ai_research_forward_uncertainties.py` (21 tests): versions, legacy
loading, forward items not blocking (live examples), disguised gaps blocking,
unchanged unknown rule, gap composition, volatility contradiction with and
without a quoted value or supplied volatility, prompt wording, service
persistence of a `COMPLETE` research with forward uncertainties, fail-closed
downgrade of a disguised gap, and removal of a contradicting volatility
unknown. Harness inspection adds AI-8C.2-R1 checks, forward items and gaps per
research. Regression: 1,687 tests and 162 subtests passed.

## 6. Acceptance criteria

1. Offline regression green on Windows and Linux (CI); Replay green.
2. One fresh LIVE session in which every new research records
   `ai-8c2-research-v2-forward-uncertainties` and no `COMPLETE` research
   contains a material gap; zero side effects.
3. Operator review of the forward uncertainties and gaps per research in
   `summary.md`, to judge whether the model separates them sensibly.
4. Reported, not required: number of `COMPLETE` research and any LONG
   TradeOpportunity created.

## 7. Acceptance evidence (2026-09-30)

Validation run on the operator machine, commit `662031c` (tree `8b6cf7ec`),
two LIVE waves (BIT:BAMI, NYSE:CDE, BIT:UCG, XETRA:EVK), 1,533 s.
Regression 1,686 passed + 1 POSIX-only skip; Replay PASS; zero side effects.

| Criterion | Result |
| --- | --- |
| Every new research records `ai-8c2-research-v2-forward-uncertainties` | PASS (10 of 10) |
| No `COMPLETE` research contains a material gap | PASS (no `COMPLETE`) |
| Volatility listed as unknown despite supplied evidence | 0 of 10 (previous run: 4 of 11) |
| AI-8C.3-R1 / R2 checks | PASS |

The model now uses `forward_uncertainties` as intended: all 47 items are
future outcomes (deal and antitrust outcomes, sustainability of growth, market
reaction). All ten research remained `PARTIAL` because every one lists at
least one genuine as-of gap. The recurring gaps are:

- peer valuation benchmarks (8 of 10 research): no evidence provider supplies
  peer or sector valuation today;
- recent quarterly margins (6 of 10): the fundamental provider supplies
  trailing (TTM) margins, not the latest quarter;
- consensus estimates and guidance (CDE, EVK, UCG).

A minority of forward-looking items are still misfiled in `unknowns`
("sustainability of revenue growth and margin expansion") and one forward item
was reclassified as a gap by the guard ("sector-wide demand for gold/silver in
2026"), both conservative.

Findings recorded for follow-up:

- `research_confidence` is 0.0 in 5 of 10 research despite MEDIUM evidence
  quality. It is produced by the model without any definition in the prompt,
  and a 0.0 value forces the confidence-adjusted score to exactly 50
  (EVK.DE `NEW_LONG`: raw 61.25, adjusted 50.0).
- The history tool records unavailable snapshots with `quality=None`; the
  replenishment runtime failed the whole wave on it. Fixed in
  `fix(e2e): route unavailable history snapshots per listing`.

Conclusion: the research contract now separates the two kinds of uncertainty
correctly. The next blocker is evidence coverage (peer valuation, latest
quarter, consensus) and the undefined research confidence, not the validator.
