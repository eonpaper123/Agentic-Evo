# Autonomous Loop v1.0 — Implementation Report

Worktree: `D:\rawle\Coding\Agentic-Evo\.lingtai\agentic-evo-total\work\dev-loop`
Branch: `agent/autonomous-loop`  HEAD: `1349de685559e942890a7b7086e2a5d63ee80767`

## 1. Files created / changed

Created:
- `src/agentic_evo/autonomous_loop.py` — v1.0 autonomous repair loop engine
  (state machine, append-only hash-chained ledger, consolidation store,
  in-memory smoke fixtures, `__main__` entry).
- `docs/engineering/autonomous-loop.md` — design / state machine / policy
  defaults / claims ceilings / smoke results (中英混合).
- `autonomous_loop_report.md` — this report.
- `autonomous-loop-smoke.jsonl` — passed-fixture cycle records (11 lines).
- `autonomous-loop-smoke-failed.jsonl` — failed-fixture cycle records (5 lines).
- `autonomous-loop-smoke-cli.jsonl` — same passed fixture run via the
  `agentic-evo autonomous-loop-smoke` CLI subcommand (wiring check).
- `smoke-passed.stdout.txt`, `smoke-passed.stderr.txt`,
  `smoke-failed.stdout.txt`, `smoke-failed.stderr.txt`,
  `cli-smoke.stdout.txt`, `cli-smoke.stderr.txt` — captured exact outputs.

Changed (worktree only; shared checkout untouched):
- `src/agentic_evo/errors.py` — added `PolicyGateError` and
  `InvalidCandidateError` (subclasses of `AgenticEvoError`).
- `src/agentic_evo/cli.py` — added the `autonomous-loop-smoke` subcommand
  (import + parser registration + dispatch to `autonomous_loop.smoke_main`).

Not modified: `tests/`, life-core modules (`kernel.py`, `trusted.py`,
`runtime.py`, `body.py`, `witness.py`), the shared checkout
`D:\rawle\Coding\Agentic-Evo`; nothing staged or committed.

## 2. Design decisions

1. **Caller-owned component.** `autonomous_loop.py` imports only `_util`,
   `errors`, and stdlib — never `runtime` / `kernel` / `trusted` / `body`.
   It writes only under its own loop home (`meta.json`, `events.jsonl`,
   `consolidation.jsonl`). No network, no subprocess, no service/UAC.
2. **Life-core protection.** Candidates are refused when `scope` falls in
   {who, why, authority, root, head}, when `touches_life_core=True`, or
   when `reversible` is not `True`. The checks run at both `propose()` and
   `probation()` (defense in depth). The loop never calls any life-core API.
3. **Policy gate.** Default `LoopPolicy(authorize_external_effects=False)`;
   `effect_kind="external"` candidates raise `PolicyGateError` unless the
   caller explicitly grants authorization. Probation window is bounded by
   `max_probe_runs` and `max_probation_seconds`.
4. **Append-only hash-chained ledger.** `events.jsonl` mirrors the
   `evidence.py` pattern: `previous_integrity_hash` + `integrity_hash` over
   canonical JSON, verified on every read (`verify()` / `records()`).
5. **Consolidation store.** `consolidation.jsonl` is a durable, append-only
   registry of reusable procedures (schema `agentic-evo-consolidation-v1`),
   written only after a committed candidate reaches
   `promotion_passes_required` consecutive passes. Consolidation is a
   procedure registry, not a policy change: the loop never mutates
   `LoopPolicy` or any runtime policy automatically.
6. **Outcome semantics.** `passed` = a probation probe passed in the window;
   `failed` = every completed probe run failed without errors;
   `inconclusive` = non-decisive probe errors, or the window exhausted before
   decisive evidence. Only these three outcomes can be recorded.
7. **Smoke fixtures (in-memory).** `passed` fixture: probe fails once, then
   passes after the candidate; two cycles both commit, then
   `pass_count=2 >= 2` triggers consolidation. `failed` fixture: a
   mis-scoped candidate leaves the defect in place; all 5 probe runs fail;
   outcome=failed; rollback; no consolidation.
8. **Entry points.** `python -m agentic_evo.autonomous_loop --fixture ...`
   (module `__main__`) and the `agentic-evo autonomous-loop-smoke` CLI
   subcommand both delegate to the same `smoke_main()`; records print as
   JSONL to stdout and are written to `--output`; a one-line summary goes to
   stderr; exit code 0 on success, 1 on expectation failure.

## 3. Claim ceilings

Established by this implementation (smoke-verified):
- Observations enter the loop as append-only records with chain integrity.
- Candidates are scoped, reversible, policy-gated, and never rewrite the
  life core.
- Probation is bounded and reversible; outcomes are restricted to
  {passed, failed, inconclusive}.
- passed → commit; failed/inconclusive → rollback without destructive
  global operations.
- Repeatedly-passing committed fixes are promoted to the durable
  consolidation store; the loop never changes production policy itself.
- Default policy performs no external side effects.

Not claimed (honest ceilings):
- No claim that the loop repairs real production defects; fix correctness is
  entirely caller-supplied (probe + candidate).
- No causal attribution, memory formation, autonomous learning, or
  self-evolution claims.
- No claim that consolidated procedures may be adopted automatically as
  production policy.
- No zero-human-intervention claim; pass counts are per loop instance
  (restored from that instance's own store on load).
- The loop does not evaluate Body semantics or life-core validity; those
  remain the runtime/kernel's responsibility.

## 4. Smoke results (exact captured outputs)

Environment: Python 3.13.14, Windows PowerShell, `PYTHONPATH=src`.

### 4a. Fixture `passed` (autonomous-loop-smoke.jsonl)

Command:

```
$env:PYTHONPATH='src'; python -m agentic_evo.autonomous_loop --fixture passed --output autonomous-loop-smoke.jsonl
```

Exit code: 0

stdout (11 JSONL records):

```
{"candidate_ref":null,"commit_ref":null,"event_id":"9326ca4ae0a141fcb4c0fb4a4f231575","event_kind":"observe","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"09c111da848d27c22a4f1acccedb2f76baa27715864cd91eb87f40725a268983","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":null,"observed_at":"2026-08-04T18:04:53.314639+00:00","occurred_at":"2026-08-04T18:04:53.314634+00:00","outcome_ref":null,"payload":{"observation":{"cycle":1,"evidence":{"broken":true,"calls":1,"fixed":false,"phase":"probe"},"probe_failed":true},"signal_kind":"probe_failure","source":"smoke-fixture-passed"},"policy_authorization":"in_memory_only","previous_integrity_hash":null,"probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":1}
{"candidate_ref":null,"commit_ref":null,"event_id":"6df042c26ed043c298b121a05fca6c6f","event_kind":"candidate","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"7ecedf856d581d89754a8d5255814708429f7bff4be7ca47980d9742c27b08c5","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":"9326ca4ae0a141fcb4c0fb4a4f231575","observed_at":"2026-08-04T18:04:53.319706+00:00","occurred_at":"2026-08-04T18:04:53.319699+00:00","outcome_ref":null,"payload":{"candidate_id":"cand-smoke-001","effect_kind":"in_memory","plan":{"applies":"clears the defect flag on the caller-owned workspace","kind":"smoke-repair","reverts":"restores the defect flag"},"policy_gate":"authorized_in_memory","reversible":true,"scope":"smoke.recurring-defect","touches_life_core":false},"policy_authorization":"in_memory_only","previous_integrity_hash":"09c111da848d27c22a4f1acccedb2f76baa27715864cd91eb87f40725a268983","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":2}
{"candidate_ref":"6df042c26ed043c298b121a05fca6c6f","commit_ref":null,"event_id":"71bf6610bf244b0190f8143529407e97","event_kind":"probation","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"075f16163d9832912235207815725471660bd2d7120df0ca6df846c1d5320426","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":"9326ca4ae0a141fcb4c0fb4a4f231575","observed_at":"2026-08-04T18:04:53.326924+00:00","occurred_at":"2026-08-04T18:04:53.326916+00:00","outcome_ref":null,"payload":{"applied":true,"apply_error":null,"candidate_id":"cand-smoke-001","probe_runs":[{"elapsed_seconds":3e-06,"error":null,"evidence":{"broken":false,"calls":2,"fixed":true,"phase":"probe"},"passed":true,"run":1}],"window_exhausted":false,"window_max_runs":5,"window_max_seconds":30.0},"policy_authorization":"in_memory_only","previous_integrity_hash":"7ecedf856d581d89754a8d5255814708429f7bff4be7ca47980d9742c27b08c5","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":3}
{"candidate_ref":"6df042c26ed043c298b121a05fca6c6f","commit_ref":null,"event_id":"44868c8ec19c447a851de0a282d00822","event_kind":"outcome","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"7ee01288eb30470bf26a3640ebb93aa01a1a794b1e9e3182a337926626f1ddd0","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":null,"observed_at":"2026-08-04T18:04:53.332330+00:00","occurred_at":"2026-08-04T18:04:53.332324+00:00","outcome_ref":null,"payload":{"candidate_id":"cand-smoke-001","outcome":"passed","probe_run_summary":{"errored":0,"failed":0,"passed":1,"total":1},"verdict_reason":"a probation probe passed within the window"},"policy_authorization":"in_memory_only","previous_integrity_hash":"075f16163d9832912235207815725471660bd2d7120df0ca6df846c1d5320426","probation_ref":"71bf6610bf244b0190f8143529407e97","protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":4}
{"candidate_ref":"6df042c26ed043c298b121a05fca6c6f","commit_ref":null,"event_id":"bb25ff28306f4c89ada3b377cd85d722","event_kind":"commit","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"32b04128ea78f56f55358ac2021f5d51b788cf85d15fe532b2e511862f653f2e","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":null,"observed_at":"2026-08-04T18:04:53.337950+00:00","occurred_at":"2026-08-04T18:04:53.337943+00:00","outcome_ref":"44868c8ec19c447a851de0a282d00822","payload":{"candidate_id":"cand-smoke-001","committed":true,"pass_count":1,"promotion_passes_required":2},"policy_authorization":"in_memory_only","previous_integrity_hash":"7ee01288eb30470bf26a3640ebb93aa01a1a794b1e9e3182a337926626f1ddd0","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":5}
{"candidate_ref":null,"commit_ref":null,"event_id":"a22a55da01b24b739a3d7c1745b6a506","event_kind":"observe","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"78f2fce17d69c99ad4419e1a0f77440e30e4a80dce5da9d98d4cb58916eaf8f9","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":null,"observed_at":"2026-08-04T18:04:53.343761+00:00","occurred_at":"2026-08-04T18:04:53.343754+00:00","outcome_ref":null,"payload":{"observation":{"cycle":2,"evidence":{"broken":true,"calls":1,"fixed":false,"phase":"probe"},"probe_failed":true},"signal_kind":"probe_failure","source":"smoke-fixture-passed"},"policy_authorization":"in_memory_only","previous_integrity_hash":"32b04128ea78f56f55358ac2021f5d51b788cf85d15fe532b2e511862f653f2e","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":6}
{"candidate_ref":null,"commit_ref":null,"event_id":"b436e3f2c9d244019ce21b7059719aa5","event_kind":"candidate","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"610b4d1282f870352de54820605dbdcfbc0f5b777e4730016213b18e4fe12505","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":"a22a55da01b24b739a3d7c1745b6a506","observed_at":"2026-08-04T18:04:53.349013+00:00","occurred_at":"2026-08-04T18:04:53.349005+00:00","outcome_ref":null,"payload":{"candidate_id":"cand-smoke-001","effect_kind":"in_memory","plan":{"applies":"clears the defect flag on the caller-owned workspace","kind":"smoke-repair","reverts":"restores the defect flag"},"policy_gate":"authorized_in_memory","reversible":true,"scope":"smoke.recurring-defect","touches_life_core":false},"policy_authorization":"in_memory_only","previous_integrity_hash":"78f2fce17d69c99ad4419e1a0f77440e30e4a80dce5da9d98d4cb58916eaf8f9","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":7}
{"candidate_ref":"b436e3f2c9d244019ce21b7059719aa5","commit_ref":null,"event_id":"460f677392434662af2590f28bbe406c","event_kind":"probation","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"6a7040157d873dc62b099ea3dc66909f650a5d8e78ba2d6388c8e910d91c0bb4","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":"a22a55da01b24b739a3d7c1745b6a506","observed_at":"2026-08-04T18:04:53.354373+00:00","occurred_at":"2026-08-04T18:04:53.354367+00:00","outcome_ref":null,"payload":{"applied":true,"apply_error":null,"candidate_id":"cand-smoke-001","probe_runs":[{"elapsed_seconds":3e-06,"error":null,"evidence":{"broken":false,"calls":2,"fixed":true,"phase":"probe"},"passed":true,"run":1}],"window_exhausted":false,"window_max_runs":5,"window_max_seconds":30.0},"policy_authorization":"in_memory_only","previous_integrity_hash":"610b4d1282f870352de54820605dbdcfbc0f5b777e4730016213b18e4fe12505","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":8}
{"candidate_ref":"b436e3f2c9d244019ce21b7059719aa5","commit_ref":null,"event_id":"82e38889f257484c886be04e2e48391a","event_kind":"outcome","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"9384877a72e9f6fbfff68f9cdbe42b9e6330d4db1a8b941aa9808d48d36ace29","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":null,"observed_at":"2026-08-04T18:04:53.360433+00:00","occurred_at":"2026-08-04T18:04:53.360428+00:00","outcome_ref":null,"payload":{"candidate_id":"cand-smoke-001","outcome":"passed","probe_run_summary":{"errored":0,"failed":0,"passed":1,"total":1},"verdict_reason":"a probation probe passed within the window"},"policy_authorization":"in_memory_only","previous_integrity_hash":"6a7040157d873dc62b099ea3dc66909f650a5d8e78ba2d6388c8e910d91c0bb4","probation_ref":"460f677392434662af2590f28bbe406c","protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":9}
{"candidate_ref":"b436e3f2c9d244019ce21b7059719aa5","commit_ref":null,"event_id":"e418ab56bbb0473abd6c71077d4622f8","event_kind":"commit","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"0a1b12b18891ac43250c18ed5a5e91db2d7dcfd2cd19e3c3f8833f8fa785c54a","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":null,"observed_at":"2026-08-04T18:04:53.367066+00:00","occurred_at":"2026-08-04T18:04:53.367060+00:00","outcome_ref":"82e38889f257484c886be04e2e48391a","payload":{"candidate_id":"cand-smoke-001","committed":true,"pass_count":2,"promotion_passes_required":2},"policy_authorization":"in_memory_only","previous_integrity_hash":"9384877a72e9f6fbfff68f9cdbe42b9e6330d4db1a8b941aa9808d48d36ace29","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":10}
{"candidate_ref":null,"commit_ref":"e418ab56bbb0473abd6c71077d4622f8","event_id":"c4cc244536e945f4b78e0b7f7b89ce73","event_kind":"consolidate","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"23afcf2b60828176f92fe0b95f10fdc00947b5284bc69c091c27f5ac292f2c91","loop_id":"c1cb4410f2bb4ce682e0ad147a20bbc2","observation_ref":null,"observed_at":"2026-08-04T18:04:53.373701+00:00","occurred_at":"2026-08-04T18:04:53.373694+00:00","outcome_ref":null,"payload":{"candidate_id":"cand-smoke-001","pass_count":2,"procedure":{"kind":"reusable-repair","procedure_id":"proc-smoke-repair-001","scope":"smoke.recurring-defect","steps":["observe probe failure","apply scoped reversible repair","probe within the bounded probation window","commit on pass"]},"procedure_id":"proc-smoke-repair-001","promotion_passes_required":2},"policy_authorization":"in_memory_only","previous_integrity_hash":"0a1b12b18891ac43250c18ed5a5e91db2d7dcfd2cd19e3c3f8833f8fa785c54a","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":11}
```

stderr summary:

```
smoke fixture=passed exit=0 kinds=observe,candidate,probation,outcome,commit,observe,candidate,probation,outcome,commit,consolidate consolidation_store_lines=1 loop_home=C:\Users\1\AppData\Local\Temp\agentic-evo-loop-smoke-whefzm1q check=passed fixture: full cycle with commit and consolidation
```

### 4b. Fixture `failed` (autonomous-loop-smoke-failed.jsonl)

Command:

```
$env:PYTHONPATH='src'; python -m agentic_evo.autonomous_loop --fixture failed --output autonomous-loop-smoke-failed.jsonl
```

Exit code: 0

stdout (5 JSONL records):

```
{"candidate_ref":null,"commit_ref":null,"event_id":"43522679b7ab486e9888075d6196eb29","event_kind":"observe","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"f1f9f28833cd21d396ac484f501fc7da846c8c4f7c546a84d596097f0e45214b","loop_id":"10cc3e30733046f1a36724fadb192e9e","observation_ref":null,"observed_at":"2026-08-04T18:04:57.120653+00:00","occurred_at":"2026-08-04T18:04:57.120647+00:00","outcome_ref":null,"payload":{"observation":{"cycle":1,"evidence":{"broken":true,"calls":1,"fixed":false,"phase":"probe"},"probe_failed":true},"signal_kind":"probe_failure","source":"smoke-fixture-failed"},"policy_authorization":"in_memory_only","previous_integrity_hash":null,"probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":1}
{"candidate_ref":null,"commit_ref":null,"event_id":"ad4e223ac91049ada01b6b7e6b41e0fc","event_kind":"candidate","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"bdd8917edac5cbfdc2eed8596e2b114a9dc54078295a51a3625bda1b48429117","loop_id":"10cc3e30733046f1a36724fadb192e9e","observation_ref":"43522679b7ab486e9888075d6196eb29","observed_at":"2026-08-04T18:04:57.125952+00:00","occurred_at":"2026-08-04T18:04:57.125944+00:00","outcome_ref":null,"payload":{"candidate_id":"cand-smoke-fail-001","effect_kind":"in_memory","plan":{"applies":"clears an unrelated flag; the defect remains","kind":"smoke-repair","reverts":"restores the unrelated flag"},"policy_gate":"authorized_in_memory","reversible":true,"scope":"smoke.mis-scoped-repair","touches_life_core":false},"policy_authorization":"in_memory_only","previous_integrity_hash":"f1f9f28833cd21d396ac484f501fc7da846c8c4f7c546a84d596097f0e45214b","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":2}
{"candidate_ref":"ad4e223ac91049ada01b6b7e6b41e0fc","commit_ref":null,"event_id":"4b31905b9d8f400fa404b7bfd61d9f2e","event_kind":"probation","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"440754181feb034b0e26d383d3bffc5a2791f697928cc5b1788fde1021f07f0b","loop_id":"10cc3e30733046f1a36724fadb192e9e","observation_ref":"43522679b7ab486e9888075d6196eb29","observed_at":"2026-08-04T18:04:57.131184+00:00","occurred_at":"2026-08-04T18:04:57.131179+00:00","outcome_ref":null,"payload":{"applied":true,"apply_error":null,"candidate_id":"cand-smoke-fail-001","probe_runs":[{"elapsed_seconds":4e-06,"error":null,"evidence":{"broken":true,"calls":2,"fixed":true,"phase":"probe"},"passed":false,"run":1},{"elapsed_seconds":1e-06,"error":null,"evidence":{"broken":true,"calls":3,"fixed":true,"phase":"probe"},"passed":false,"run":2},{"elapsed_seconds":1e-06,"error":null,"evidence":{"broken":true,"calls":4,"fixed":true,"phase":"probe"},"passed":false,"run":3},{"elapsed_seconds":1e-06,"error":null,"evidence":{"broken":true,"calls":5,"fixed":true,"phase":"probe"},"passed":false,"run":4},{"elapsed_seconds":0.0,"error":null,"evidence":{"broken":true,"calls":6,"fixed":true,"phase":"probe"},"passed":false,"run":5}],"window_exhausted":false,"window_max_runs":5,"window_max_seconds":30.0},"policy_authorization":"in_memory_only","previous_integrity_hash":"bdd8917edac5cbfdc2eed8596e2b114a9dc54078295a51a3625bda1b48429117","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":3}
{"candidate_ref":"ad4e223ac91049ada01b6b7e6b41e0fc","commit_ref":null,"event_id":"e03bd687e634435ab0797af8910a984a","event_kind":"outcome","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"753f4a9439c7c64ec73217045ecb57fdaf8f7c223534439c1091721a1ce29fad","loop_id":"10cc3e30733046f1a36724fadb192e9e","observation_ref":null,"observed_at":"2026-08-04T18:04:57.136739+00:00","occurred_at":"2026-08-04T18:04:57.136733+00:00","outcome_ref":null,"payload":{"candidate_id":"cand-smoke-fail-001","outcome":"failed","probe_run_summary":{"errored":0,"failed":5,"passed":0,"total":5},"verdict_reason":"every completed probe run failed within the window"},"policy_authorization":"in_memory_only","previous_integrity_hash":"440754181feb034b0e26d383d3bffc5a2791f697928cc5b1788fde1021f07f0b","probation_ref":"4b31905b9d8f400fa404b7bfd61d9f2e","protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":4}
{"candidate_ref":"ad4e223ac91049ada01b6b7e6b41e0fc","commit_ref":null,"event_id":"e10f45137bbd46e08e5e825e953c5586","event_kind":"rollback","instrument_version":"agentic-evo-autonomous-loop-smoke-1","integrity_hash":"0299e89cda12bea0d2f0fe7a60385dd4a245b4e7366d5c55f65197f0d01f0acc","loop_id":"10cc3e30733046f1a36724fadb192e9e","observation_ref":null,"observed_at":"2026-08-04T18:04:57.142560+00:00","occurred_at":"2026-08-04T18:04:57.142553+00:00","outcome_ref":"e03bd687e634435ab0797af8910a984a","payload":{"candidate_id":"cand-smoke-fail-001","destructive_global_ops":false,"reason":"failed","revert_error":null,"reverted":true},"policy_authorization":"in_memory_only","previous_integrity_hash":"753f4a9439c7c64ec73217045ecb57fdaf8f7c223534439c1091721a1ce29fad","probation_ref":null,"protocol_version":"autonomous-loop-v1.0","schema_version":"agentic-evo-autonomous-loop-v1","sequence":5}
```

stderr summary:

```
smoke fixture=failed exit=0 kinds=observe,candidate,probation,outcome,rollback consolidation_store_lines=0 loop_home=C:\Users\1\AppData\Local\Temp\agentic-evo-loop-smoke-07eu6ein check=failed fixture: rollback with no consolidation
```

### 4c. CLI subcommand wiring check

Command:

```
$env:PYTHONPATH='src'; python -m agentic_evo.cli autonomous-loop-smoke --fixture passed --output autonomous-loop-smoke-cli.jsonl
```

Exit code: 0

stderr summary:

```
smoke fixture=passed exit=0 kinds=observe,candidate,probation,outcome,commit,observe,candidate,probation,outcome,commit,consolidate consolidation_store_lines=1 loop_home=C:\Users\1\AppData\Local\Temp\agentic-evo-loop-smoke-0lhlx130 check=passed fixture: full cycle with commit and consolidation
```

### 4d. Policy gate / life-core guard checks (direct run)

```
GATE: external denied -> external side effects are denied by the default loop policy;
CORE: life-core refused -> candidate scope 'head' overlaps the life core
AUTH: external authorized when granted
```

## 5. Verification notes

- `autonomous-loop-smoke.jsonl`: 11 lines; kinds
  `[observe, candidate, probation, outcome, commit, observe, candidate,
  probation, outcome, commit, consolidate]`; outcomes `[passed, passed]`;
  consolidate records = 1; consolidation store lines = 1.
- `autonomous-loop-smoke-failed.jsonl`: 5 lines; kinds
  `[observe, candidate, probation, outcome, rollback]`; outcome `[failed]`;
  consolidate records = 0; consolidation store lines = 0; rollback records
  `reverted=true, destructive_global_ops=false`.
- `loop.verify()` (hash-chain integrity) passes in both runs.
- Byte-compile of all changed modules passes (`python -m py_compile`).
- `git status --short` shows only the expected modified files
  (`src/agentic_evo/cli.py`, `src/agentic_evo/errors.py`) and the new
  untracked artifacts; nothing staged, nothing committed, `tests/` untouched.

## 6. Remaining risks / future work

- Pass counting is per loop instance and restored from that instance's own
  store; cross-instance promotion would need an explicit shared registry.
- The engine trusts the caller's `apply`/`revert` implementations within the
  declared contract; a malicious or buggy candidate could still corrupt a
  caller-owned workspace (mitigated by scoping + reversibility declarations
  + policy gate; the engine cannot sandbox arbitrary callables).
- `inconclusive` path is implemented (probe errors / window exhaustion) but
  not smoke-demonstrated; a dedicated fixture can be added later.
