# Slice C — Real-task autonomous-loop integration demo: report

Date: 2026-08-05 (worktree `work/dev-loop-demo`, branch `agent/loop-demo`, base 1f886b0).
Nothing staged or committed. Only the files listed below were added/changed; no
`trusted/` `body/` `kernel/` `runtime/` `witness/` `evidence.py`
`autonomous_loop.py` or existing tests were modified.

## 1. Files created / changed

| File | Status | Purpose |
| --- | --- | --- |
| `src/agentic_evo/loop_integration.py` | new | Caller-owned integration: `DefectWorkspace.create`, `build_candidates`, `run_loop_demo` (pure stdlib; imports only `autonomous_loop` + `_util`) |
| `src/agentic_evo/cli.py` | modified | Added `loop-demo-run` subcommand (JSON output; exit 0 success / 6 run error / 7 unfixed) |
| `tests/test_loop_integration.py` | new | 6 unittest cases (workspace creation, full cycle + chain, rollback bytes, rerunnable, policy gate, import-boundary AST check) |
| `docs/engineering/autonomous-loop-integration.md` | new | Chinese integration doc (what/how-to-run/CLI/claim ceilings) |
| `loop_integration_report.md` | new | This report |
| `test-loop-integration.stdout.txt` | new | Captured verbatim unittest output |
| `loop-demo-cli.stdout.txt` / `loop-demo-cli.stderr.txt` | new | Captured CLI smoke output (JSON) / empty stderr |

`git status --short` at completion:

```
 M src/agentic_evo/cli.py
?? loop-demo-cli.stderr.txt
?? loop-demo-cli.stdout.txt
?? src/agentic_evo/loop_integration.py
?? test-loop-integration.stdout.txt
?? tests/test_loop_integration.py
```

## 2. Verification: py_compile

```powershell
& 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -S -m py_compile src\agentic_evo\loop_integration.py src\agentic_evo\cli.py tests\test_loop_integration.py
```

Exit code: **0** (no output, no errors).

## 3. Verification: test suite (verbatim)

Runtime interpreter verified present: `C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` (Python 3.12.13).

Command: `$env:PYTHONPATH='src'; & 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tests.test_loop_integration -v`

Exit code: **0**. Verbatim output:

```
test_caller_owned_import_boundary (tests.test_loop_integration.LoopIntegrationTests.test_caller_owned_import_boundary) ... ok
test_failed_candidate_rollback_restores_original_bytes (tests.test_loop_integration.LoopIntegrationTests.test_failed_candidate_rollback_restores_original_bytes) ... ok
test_full_demo_cycle_final_module_passes_and_chain_verifies (tests.test_loop_integration.LoopIntegrationTests.test_full_demo_cycle_final_module_passes_and_chain_verifies) ... ok
test_policy_gate_refuses_life_core_scope_and_unauthorized_external (tests.test_loop_integration.LoopIntegrationTests.test_policy_gate_refuses_life_core_scope_and_unauthorized_external) ... ok
test_rerunnable_same_final_outcome_on_fresh_dirs (tests.test_loop_integration.LoopIntegrationTests.test_rerunnable_same_final_outcome_on_fresh_dirs) ... ok
test_workspace_failing_test_fails_and_correct_patch_passes (tests.test_loop_integration.LoopIntegrationTests.test_workspace_failing_test_fails_and_correct_patch_passes) ... ok

----------------------------------------------------------------------
Ran 6 tests in 2.512s

OK
```

## 4. Verification: CLI smoke (verbatim)

Command:

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m agentic_evo.cli loop-demo-run `
  --workspace-dir "$env:TEMP\agentic-evo-loop-demo-cli\ws" --loop-home "$env:TEMP\agentic-evo-loop-demo-cli\home"
```

Exit code: **0**. stdout (single JSON line, verbatim):

```json
{"ok":true,"result":{"candidate_ids":["cand-window-wrong-fix","cand-window-correct-fix"],"candidate_outcomes":{"cand-window-correct-fix":"passed","cand-window-wrong-fix":"failed"},"consolidation_count":1,"consolidation_path":"C:\\Users\\1\\AppData\\Local\\Temp\\agentic-evo-loop-demo-cli\\home\\consolidation.jsonl","defect":"off_by_one","event_kinds":["observe","candidate","probation","outcome","rollback","candidate","probation","outcome","commit","observe","candidate","probation","outcome","commit","consolidate"],"events_path":"C:\\Users\\1\\AppData\\Local\\Temp\\agentic-evo-loop-demo-cli\\home\\events.jsonl","final_module_passed":true,"final_status":"consolidated","final_test_exit_code":0,"loop_home":"C:\\Users\\1\\AppData\\Local\\Temp\\agentic-evo-loop-demo-cli\\home","outcome_counts":{"failed":1,"inconclusive":0,"passed":2},"workspace":"C:\\Users\\1\\AppData\\Local\\Temp\\agentic-evo-loop-demo-cli\\ws"}}
```

stderr: empty. The chain shows one rolled-back candidate (`cand-window-wrong-fix`),
then the correct candidate committed twice (pass_count 2 >= promotion_passes_required 2)
and consolidated once.

## 5. Additional determinism evidence

Three consecutive full demo runs on fresh dirs in one process:

```
all pass: True
all consolidated: True
outcomes: [{'passed': 2, 'failed': 1, 'inconclusive': 0}] x3
event_kinds: observe,candidate,probation,outcome,rollback,candidate,probation,outcome,commit,observe,candidate,probation,outcome,commit,consolidate
```

## 6. Claim ceilings (honest)

Established:
- The demo defect is **synthetic** (deliberately planted) but the flow is **real**:
  actual files, actual `python -m unittest` execution, actual patch apply/revert
  (byte-identical restore on rollback), actual consequence reading, rerunnable evidence.
- The loop drives a real task flow end-to-end and consolidates a reusable procedure.
- Policy gate works: life-core scope refused (`InvalidCandidateError`), unauthorized
  external effects refused by default policy (`PolicyGateError`).

NOT claimed:
- Candidate generation is a **scripted stub** (caller supplies the two patches); the
  engine does not generate candidates. LLM-generated candidates and zero-human-direction
  candidate design remain future work (Sol quota pending).
- Fixing real production defects; agent-authored candidates; learning/self-evolution;
  production policy change.

## 7. Design decisions

1. **`run_tests()` purges `__pycache__` before each probe run** — Windows pyc validity
   is mtime(seconds)+size; same-second, same-size patches could otherwise let the probe
   load stale bytecode and mask a correct patch (observed intermittently during
development; see Risks). The wrong-fix line length is also kept distinct from both the
   buggy and correct lines as a second line of defense.
2. **Wrong fix first** — ordered candidates exercise both rollback and commit paths.
3. **One observation drives several candidate attempts** (shared `observation_ref`),
   matching the engine's design (each `propose` can reference the same observation).
4. **Promotion rerun** — the first passing candidate runs one more full cycle so
   pass_count reaches `promotion_passes_required` (default 2) before `consolidate`;
   with `promotion_passes_required=1` the demo consolidates after the first commit.
5. **Demo policy authorizes external effects** (`LoopPolicy(authorize_external_effects=True)`)
   because candidates patch real caller-owned files; engine default (no external effects)
   is preserved and tested.
6. **`unittest` writes to stderr** by default — probe evidence and tests treat
   stdout+stderr as the test output.

## 8. Remaining risks

- **Stale-bytecode masking on Windows**: mitigated by the `__pycache__` purge + distinct
  patch line lengths, but any future demo that patches a module without purging the cache
  could reproduce the intermittency. Documented in `loop_integration.py` docstring.
- **Subprocess cost**: each probe spawns a fresh interpreter (`python -m unittest`); the
  full demo makes ~10 subprocess calls. Slow machines could approach
  `max_probation_seconds` (default 30 s); acceptable for the demo, worth noting for scale-up.
- **Scripted stub**: consolidation writes a procedure with scripted steps; it is evidence
  of the *mechanism*, not of candidate design quality.
- **Windows-only testing**: verified on this machine only; no CI or cross-platform run.
