# Slice E report — Genesis CLI capability (no real execution)

Worktree: `<AGENTIC_EVO_REPO>\.lingtai\agentic-evo-total\work\dev-genesis`
(branch `agent/genesis`, base `94f2823` — the integration tip). Nothing was staged or committed.
Spec: `../slice_e_genesis_design.md` (followed strictly).

## 1. Files created / changed

| Path | Status | Notes |
| --- | --- | --- |
| `src/agentic_evo/cli.py` | changed (+108) | Added the `genesis` subcommand: `_genesis_arguments()` (--home/--host-binding/--purpose-anchor/--root/--initial-head), `_genesis()` handler (refuse existing Genesis, dev-home, or non-empty home → exit 6; perform `TrustedState.genesis(...)`; JSON receipt with who/why/root/head/authority/evidence_ref/home), registration in `_parser()`, dispatch in `main()`. Existing commands untouched. |
| `tests/test_genesis_cli.py` | **new** | 5 unittest cases (see §3). |
| `docs/engineering/genesis.md` | **new** | Chinese engineering doc: what Genesis is, parameters, how to run, irreversible note, host-binding semantics, claim ceilings. |
| `genesis_report.md` | **new** | This file. |

`git status --porcelain` (verbatim):

```
 M src/agentic_evo/cli.py
?? tests/test_genesis_cli.py
?? docs/engineering/genesis.md
?? genesis_report.md
```

`git diff --stat` (verbatim):

```
 src/agentic_evo/cli.py | 109 ++++++++++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 108 insertions(+), 1 deletion(-)
```

No other existing file was modified; nothing in `trusted/`, `body/`, `kernel/`, `runtime/`,
`witness/`, `evidence/`, `adapters/` or the pre-existing tests was touched.

## 2. Design decisions

- **Trusted state lives at `--home` directly** (state.sqlite3 + witness.key at home root), matching
the brief's wording `TrustedState.load(home)`. This is distinct from the runtime layout
(`home/trusted`): this slice deliberately performs the bare `TrustedState.genesis(...)` ceremony;
Body/runtime wiring is a later slice.
- **Refusal order** (all exit 6, error JSON on stderr): `TrustedState.has_genesis(home)` →
`TrustedState.has_genesis(home / "trusted")` (dev-home / runtime-layout marker) →
non-empty home. A dev-home made by `DevelopmentalRuntime.genesis(home)` contains `trusted/` +
`body/`, so it is caught by the non-empty check and, when `home/trusted/state.sqlite3` exists,
by the explicit nested check.
- **Receipt fields are the actual state-row fields** read back via `TrustedState.snapshot()`
(who/why/root/head/authority from `KernelSnapshot`) plus `evidence_ref` pointing at the genesis
evidence record (`{sequence, event_id, integrity_hash}`) and the resolved `home`.
- **Versions**: instrument_version `agentic-evo-cli-genesis-v1`; protocol_version =
`TRUSTED_SCHEMA_VERSION` (`agentic-evo-trusted-v2`); genesis payload
`{trusted_schema, source: "genesis_cli"}` (bounded, non-sensitive).
- **Authority check test** uses the TrustedState API gate: `set_authority(authority="on",
host_binding=<wrong>)` raises `AuthorityError` (same check the runtime's `turn_on` uses).
- **Tests run the CLI as a real subprocess** (`sys.executable -P -m agentic_evo.cli genesis …`,
cwd=repo root, PYTHONPATH=src), matching the existing `test_cli_lifecycle.py` pattern; all homes
are temp dirs. No real Genesis was executed.

## 3. Verbatim test output and exit codes

Interpreter verified first:
`C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` → `Python 3.12.13`.

py_compile (`-S` as interpreter flag, before `-m`):

```powershell
$env:PYTHONPATH='src'; & 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -S -m py_compile src/agentic_evo/cli.py tests/test_genesis_cli.py
```
→ exit 0, no output.

Required unit-test command:

```powershell
$env:PYTHONPATH='src'; & 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tests.test_genesis_cli -v
```

Output (verbatim; unittest writes to stderr):

```
test_fresh_home_genesis_exit_zero_receipt_and_state_row (tests.test_genesis_cli.GenesisCLITests.test_fresh_home_genesis_exit_zero_receipt_and_state_row) ... ok
test_genesis_evidence_recorded_and_receipt_ref_matches (tests.test_genesis_cli.GenesisCLITests.test_genesis_evidence_recorded_and_receipt_ref_matches) ... ok
test_receipt_json_parses_with_expected_keys (tests.test_genesis_cli.GenesisCLITests.test_receipt_json_parses_with_expected_keys) ... ok
test_second_genesis_refused_exit_6_and_state_unchanged (tests.test_genesis_cli.GenesisCLITests.test_second_genesis_refused_exit_6_and_state_unchanged) ... ok
test_wrong_host_binding_load_fails_authority_check (tests.test_genesis_cli.GenesisCLITests.test_wrong_host_binding_load_fails_authority_check) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.589s

OK
```

Manual CLI smoke (temp home, verbatim):

Fresh home → exit 0:

```
{"ok":true,"result":{"authority":"on","evidence_ref":{"event_id":"7835ac2fe72a4ea3923b8d2dd4708334","integrity_hash":"e2b148b12c28e214392499632ebbf3a517758b6793ca6cc99f03e5a09d458940","sequence":1},"head":"bbbb…","home":"C:\\Users\\1\\AppData\\Local\\Temp\\ae-genesis-smoke-home","root":"aaaa…","who":"ffcffe70…","why":"bff14548…"}}
```

Same home again → exit 6, stderr: `{"error":{"code":"genesis_error","message":"home already contains a Genesis"},"ok":false}`

Non-empty home → exit 6, stderr: `{"error":{"code":"genesis_error","message":"home is not an empty state directory"},"ok":false}`

Regression check (full suite): `python -m unittest discover -s tests -v` ran 280 tests;
237 passed, 43 errors, all pre-existing **environmental** failures in
`tests/test_windows_gate_b_evidence.py` (42 subtest errors) and `tests/test_windows_gate_b.py`
(1 error) — verified identical on a clean `git worktree add` of base `94f2823` (control run:
the same modules fail with the same errors; root cause is subprocess-output decoding of
non-UTF-8 console bytes in this sandbox). None of the failures involve `cli.py`, `trusted.py`,
or the genesis slice; all 5 new tests pass. Excluding those two modules, the remaining 244 tests
(239 base + 5 new) pass.

## 4. Honest claim ceilings

- This slice proves the Genesis **capability** works and is tested (exit 0 receipt, who/why hashing,
evidence recording, second-genesis refusal exit 6 with unchanged state, authority gate on wrong
host binding, JSON receipt shape).
- It does **NOT** perform or claim a real Genesis, and does **NOT** claim any v1.0 requirement
completion. Real execution is a separate parent step requiring the operator parameter confirmation.
- Genesis creates identity only; it does not by itself prove install/uninstall, Gate B,
second-agent, or longitudinal learning (see `docs/engineering/genesis.md` §6).

## 5. Remaining risks

- The genesis home is a bare TrustedState domain; `DevelopmentalRuntime.load(home)` will not work
until Body wiring (a later slice) provides `body/` + the runtime layout.
- `witness.key` irreversibility: loss of the key makes the domain unverifiable — documented.
- Full-suite green status in this sandbox is blocked by the pre-existing Windows gate B evidence
environment failures (verified pre-existing at base `94f2823`); they are unrelated to this slice.
