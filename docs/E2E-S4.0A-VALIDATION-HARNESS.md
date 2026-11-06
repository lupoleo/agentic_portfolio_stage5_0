# E2E-S4.0A — Validation Harness

| Field | Value |
| --- | --- |
| Type | Operational tooling (no business contract change) |
| Branch | `e2e-s4.0a-validation-harness` |
| Entry points | `scripts/Invoke-Stage4Validation.ps1`, `python -m tools.stage4_validation` |

## Purpose

Provide one repeatable command, runnable from the VS Code PowerShell terminal,
that validates the Stage 4 system at three levels without any risk to the real
state database, the real portfolio or a broker.

| Level | What runs | Network | Writes |
| --- | --- | --- | --- |
| `Unit` | Full offline regression (`pytest`, JUnit XML) | none | none |
| `Replay` | `CACHE_ONLY` resume of a persisted root S4.0A run | blocked at socket level | sandbox copy only |
| `Live` | Real bounded candidate replenishment CLI (Yahoo + local Ollama) | Yahoo, Ollama | sandbox copy only |

## Safety model

1. The state database is copied with the SQLite backup API from a read-only
   connection. The SHA-256 of `data/state/portfolio_cio.db` is recorded before
   and after every level and must be unchanged.
2. Sandboxes are created under the OS temporary directory, outside OneDrive.
3. `Replay` rejects persisted runs whose database path is absolute, because the
   sandbox could not be substituted without changing the run identity.
4. `Replay` patches `socket.connect`, `socket.connect_ex` and
   `socket.create_connection`; any attempt is recorded and fails the level.
   This proves zero provider calls and zero LLM calls, not only the persisted
   `network_calls` counter.
5. Every level verifies `broker_orders_submitted = portfolio_mutations =
   automatic_executions = 0` and that no Execution Plan was created.

## Replay identity

The run identity is a fingerprint of the persisted request and runtime
configuration. Both contain path strings, so `Replay` rebuilds them verbatim
from the root report instead of re-deriving them through the CLI. The sandbox
reproduces the original relative layout so that the same strings resolve to
the copied inputs. The level fails if the configuration fingerprint or the run
ID differs from the persisted ones.

Known limitation: identities remain host-path dependent (see the S4.0A
evaluation). Replay across machines works only because the harness rebuilds
the exact strings.

## Usage

```powershell
# Offline: Unit + Replay (about one minute)
.\scripts\Invoke-Stage4Validation.ps1

# One LIVE wave on top (needs Ollama with qwen3:8b and internet access)
.\scripts\Invoke-Stage4Validation.ps1 -Level All -MaxWaves 1

# Pin the root run explicitly
.\scripts\Invoke-Stage4Validation.ps1 -Level Replay,Live -RunId s4a-114bae62749acb7b6f14169f
```

Without `-RunId` the most recent root report in
`data/cache/e2e/stage4/first_complete_e2e/` is used.

Results are written to `data/cache/e2e/stage4/validation/<UTC stamp>/`
(git-ignored): `summary.md`, `summary.json`, per-level JSON, logs, JUnit XML
and the PowerShell transcript.

Exit code `0` means every executed level passed. For `Live`, a
`VALID_NON_COMPLETE` replenishment (CLI exit code 2) is a pass: the harness
validates safety and integrity, not whether the market offered an
opportunity.

## Research inspection

`Live` attaches an inspection of everything the session persisted in the
sandbox: TECHNICAL evidence (adapter and contract version, whether volatility
is stated), research (status, quality, confidence, material and other
unknowns), scores and outcome reasons. It also evaluates the AI-8C.3-R1
acceptance checks as a separate `r1_acceptance` verdict, which does not change
the safety verdict of the level.

The same inspection can be run on any database, for example an earlier live
sandbox:

```powershell
python -m tools.stage4_validation inspect --database <sandbox>\data\state\portfolio_cio.db --since 2026-09-30T12:25:55Z
```

## Shadow ledger export

After the `Live` level the harness exports every scored directional
hypothesis of the sandbox into `data/state/shadow_ledger.db`
(`docs/E2E-S4.0B-SHADOW-LEDGER.md`), labelled `validation:<stamp>`. The
export is idempotent and never fails the level; `-NoShadowLedger` disables it.

## Regression baseline

`pytest.ini` restricts collection to `tests/`. The previously reported
1,620 tests included 18 duplicates collected from git-ignored or patch
folders (`payload/`, `tools/pf_1e2_service_persistence_integration/`). The
canonical baseline before this branch is 1,602 tests and 162 subtests.
