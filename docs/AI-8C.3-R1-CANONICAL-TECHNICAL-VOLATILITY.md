# AI-8C.3-R1 — Canonical Technical Volatility

| Field | Value |
| --- | --- |
| Type | Reopening of a frozen contract |
| Reopened contract | AI-8C.3 — Opportunity Scoring (canonical technical input) |
| Also affected | E2E-S4.0A.3 technical evidence adapter (orchestration layer) |
| Branch | `e2e-s4.0a-validation-harness` (separate commit) |
| Status | ACCEPTED — live evidence 2026-09-30 |
| Approved by | Operator, 2026-09-30 |

## 1. Reason

Stage 2 computes a deterministic 20-session annualized volatility
(`TechnicalAnalysis.volatility_20d_pct`), but the frozen
`CanonicalTechnicalInput` did not carry it. The Research model therefore never
received volatility evidence, while `ResearchCoverageValidator` classifies any
unknown mentioning `volatility` as material and incompatible with `COMPLETE`.
The research prompt even cites "technical volatility is unknown" as an
acceptable narrower unknown when RSI and moving averages are supplied.

The system was penalizing a data gap it created itself.

Evidence from the operator state database (research created from
2026-09-28 onward, 151 records):

| Material unknowns present | Research |
| --- | ---: |
| Volatility together with other material terms | 72 |
| Other material terms, no volatility | 67 |
| No material unknowns | 9 |
| Volatility only | 3 |

Across all 191 persisted research records, 189 are `PARTIAL`, one is
`INSUFFICIENT_EVIDENCE` and one is `COMPLETE`. The most frequent material
terms in recent unknowns are earnings (86 records), revenue (85), sector (82),
volatility (75), valuation (58), margin (51) and peer (50).

## 2. Change

1. `CanonicalTechnicalInput` gains `volatility_20d_pct: float | None` and an
   explicit `contract_version = "ai-8c3-canonical-technical-v2"`. A value must
   be finite and non-negative; an undefined Stage-2 value becomes `None`. It is
   never estimated or defaulted.
2. `CanonicalTechnicalEvidenceAdapter` states
   `20-session annualized volatility: X%.` when the value exists, omits it
   otherwise, records the contract version in source metadata and bumps
   `adapter_version` to `stage4-research-technical-v2`.
3. Opportunity Scoring exposes `volatility_20d_pct` and
   `technical_input_contract` in the canonical technical feature block. The
   default `prompt_version` becomes
   `opportunity-scoring-v12-canonical-technical-volatility`.

Unchanged by design:

- the deterministic technical base score and its bounded AI adjustment;
- every research, completeness, quality, score and confidence threshold;
- the research prompt (AI-8C.2) and `ResearchCoverageValidator` terms;
- all Stage 4 orchestration, portfolio, risk and execution contracts.

## 3. Impact analysis

- New evidence text produces new deterministic evidence IDs. Persisted v1
  evidence, research, scores and outcomes are neither rewritten nor
  reinterpreted.
- Terminal hypotheses are not re-run on resume, so the change applies to new
  replenishment sessions and new hypotheses only.
- Existing root-run identities are unaffected: run fingerprints do not include
  adapter or prompt versions. The validation harness replay confirms this.
- The scoring prompt grows by two short lines and stays below its 14k
  character guard.
- The technical score cannot move because of volatility: the base score
  ignores it by construction (covered by a test).

Expected effect, stated conservatively: supplying volatility removes the
volatility unknown from 75 of the 151 recent records, but only 3 of them had
volatility as their sole material unknown. This change is necessary but not
sufficient for `COMPLETE` research. The next dominant blockers are sector/peer
context and fundamentals (earnings, revenue, margins, valuation), which
require evidence, not threshold relaxation.

## 4. Migration

No data migration. Versions are visible in persisted outputs through the
evidence `adapter_version`, the source metadata `canonical_technical_contract`
and the scoring inference `prompt_version`. Mixed v1/v2 populations in the
same database remain distinguishable.

## 5. Tests

`tests/test_ai_canonical_technical_volatility.py` covers exact Stage-2 parity,
`None` for undefined values, contract rejection of invalid values, adapter
text/omission/versioning, determinism, scoring feature exposure, base-score
invariance and the prompt version bump. Two existing assertions of the default
scoring prompt version were updated. Regression: 1,627 tests and
162 subtests passed.

## 6. Acceptance criteria

1. Offline regression green on Windows and Linux (CI).
2. Validation harness `Replay` green: root run identity reproduced, zero
   network, zero new inference, zero side effects.
3. One fresh LIVE replenishment session (`-Level All`) in which newly persisted
   TECHNICAL evidence carries `stage4-research-technical-v2` and the volatility
   statement, and no new research lists volatility as unknown when the
   evidence contains it.
4. Zero broker orders, portfolio mutations and automatic executions.

A selectable `TradeOpportunity` is not an acceptance criterion: it depends on
evidence the market and providers supply, and producing one by relaxing a
gate would violate the Stage 4 invariants.

## 7. Acceptance evidence (2026-09-30)

Validation run `20260930T122357Z` on the operator machine (Windows, Python
3.10.11, branch `e2e-s4.0a-validation-harness`):

| Criterion | Result |
| --- | --- |
| Offline regression | PASS: 1,626 passed, 1 skipped (POSIX-only harness test), 162 subtests |
| Replay | PASS: `s4a-114bae62749acb7b6f14169f` reproduced, configuration fingerprint `12dc605b…`, zero sockets, zero new inference |
| Live replenishment | PASS: session `s4a1-f113b073b4df6b7a8ec31b8a`, wave BIT:UCG + NYSE:AGO, four directional hypotheses, zero side effects, production database hash unchanged |
| TECHNICAL evidence | 4 of 4 items `stage4-research-technical-v2`, contract `ai-8c3-canonical-technical-v2`, volatility stated in all |
| Research unknowns | 0 of 4 research list volatility as unknown (before R1: 75 of 151) |
| Score uncertainty factors | 0 of 4 scores list volatility as uncertain |

All four research remained `PARTIAL` (`RESEARCH_NOT_COMPLETE`). Of the eleven
remaining material unknowns, one is an evidence gap (latest AGO earnings
details); the others are forward-looking uncertainties (future earnings,
valuation sustainability, M&A impact) that no additional as-of evidence can
remove. This is outside the AI-8C.3 scope and is recorded as the next
research-contract finding.
