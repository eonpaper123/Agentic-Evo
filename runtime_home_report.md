# Slice H report — make the Genesis-born home servable by the runtime (identity preserved)

Worktree: `D:\rawle\Coding\Agentic-Evo\.lingtai\agentic-evo-total\work\dev-genesis` (branch `agent/genesis`; nothing staged/committed).
Spec: `../slice_h_runtime_adopt_design.md` (followed; one honest deviation documented in §2.3).
Date: 2026-08-05.

## 1. Investigation findings (READ-FIRST evidence)

### 1.1 The failure reproduced exactly

Real home `D:\rawle\Coding\Agentic-Evo\.agentic-evo\runtime-home` contains ONLY:

```
state.sqlite3   24576 bytes
witness.key        32 bytes
```

(1) `serve` on the real home fails as the brief predicted (the CLI flag is `--dev-home`, not `--home`; with the real flag):

```powershell
$env:PYTHONPATH='src'; python -m agentic_evo.cli serve --dev-home D:\rawle\Coding\Agentic-Evo\.agentic-evo\runtime-home
```

```json
{"error": {"code": "not_initialized", "message": "the development runtime has no existing trusted state"}, "ok": false}
```
→ exit code **3**. (`service.main` requires `home/trusted/state.sqlite3`; the genesis CLI writes state at the home root.)

(2) `DevelopmentalRuntime.load(home)` → `IntegrityError: trusted witness key is unavailable` because `TrustedState.load(home/trusted)` finds no `witness.key` under `trusted/` (full traceback captured during investigation).

(3) `read_manifest(head)` on the genesis head would raise `BodyNotFoundError` (no `body/` store; no manifest).

### 1.2 Root-cause chain (three independent gaps)

| # | Gap | Where |
| --- | --- | --- |
| G1 | Genesis CLI writes `state.sqlite3` + `witness.key` at the **home root**; the runtime requires them under `home/trusted/` | `cli.py::_genesis` → `TrustedState.genesis(home, ...)` vs `runtime.py::DevelopmentalRuntime.__init__` (`runtime_path = home / "trusted"`) |
| G2 | The genesis CLI creates **no Body store** (`home/body/`); the runtime validates the current Body at every load/status (`_validate_current_body` → `read_manifest(head)` + `_bind_manifest`) | `cli.py::_genesis` vs `runtime.py` |
| G3 | **The real born `head` is NOT a Body commitment.** `state.sqlite3` row: `head = ee79ae49b8941344e1f314e1f9183deacf8c089d` (40-hex git SHA), `root = agentic-evo-root-v1`. Body commitments are `sha256(canonical manifest)` = 64-hex, and `body.py::read_manifest` enforces `sha256(manifest bytes) == filename`; additionally `body_process.py` boot verification enforces `sha256(exported manifest) == boot.head` where `boot.head == status.head` (`spawn_current`). **A 40-hex string can never equal a sha256 commitment**, so the pinned birth head cannot be served as-is, period. | `trusted.py` state row; `body.py:123-131`; `body_process.py:322`; `service.py::_ensure_body` |

### 1.3 Why the parent design's literal plan is impossible on the real home

The brief assumed the genesis `head` is a body commitment that survives a layout move ("layout moves do not change root/head strings"). True for runtime-born homes (`DevelopmentalRuntime.genesis` commits the initial Body and stores its commitment as head). False for the **CLI-born real home**: slice-E's `genesis` accepts arbitrary pinned strings, and the real home was born with a git-SHA head. Keeping `head` byte-identical would require `sha256(x) == ee79ae49…` (40-hex) — impossible — and the Body subprocess boot protocol (`sha256(manifest) == head`) makes serve fundamentally incompatible with a non-body head. The only implementations are (a) change the head with a witnessed record, or (b) relax core integrity invariants in `body.py`/`runtime.py`/`body_process.py`. (a) is the cleanest, uses only existing public APIs, and keeps every integrity check intact.

## 2. Fix design — `agentic-evo runtime-adopt` (zero core-file changes)

New `src/agentic_evo/runtime_adopt.py` + additive CLI wiring in `cli.py` (`runtime-adopt --home`; exit 0 / exit 6 `runtime_adopt_error`). **`trusted.py`, `body.py`, `kernel.py` untouched.** Steps:

1. Validate: home has `state.sqlite3`+`witness.key` at root, `TrustedState.has_genesis(home)` (full verify), no `trusted/state.sqlite3`+`witness.key` pair, no `body/`.
2. Baseline: `TrustedState.load(home)` → snapshot (who/why/root/head/authority) + records (genesis seq 1).
3. Commit the **initial Body** for the born lineage via existing `BodyStore.commit`: `root = born root`, `parent_head = pinned genesis head`, `author_kind = research_instrument`, `activation_kind = surface-context-utf8-v1`, `activation_artifact = entrypoint.md`, files `entrypoint.md` + `identity.md` → real sha256 commitment `C`.
4. **Witness the head rebinding** via existing `TrustedState.advance_head(expected_head=pinned genesis head, candidate=initial manifest, author_kind=research_instrument, ingress_path=runtime_adopt)` → new `head_advanced` evidence record (seq 2); who/why/root untouched; `state.head = C`.
5. Verify post-state: who/why/root identical, head == C, records == genesis + head_advanced, genesis record byte-identical.
6. Relocate trusted state: copy `state.sqlite3`+`witness.key` into `trusted/`, re-verify from `trusted/`, then remove the root originals (witness key bytes preserved).
7. Prove servable: `DevelopmentalRuntime(home)` loads, `status()` binds root/head/activation.

Identity semantics (honest): `who`/`why`/`root` byte-identical before/after (root also written into the Body manifest). `head` necessarily moves to the initial Body commitment; the pinned genesis head is preserved **as the lineage parent** (`manifest.parent_head`) and as the genesis record's `head_after`. Genesis evidence (seq 1) preserved; `records()` = 2 (genesis + head_advanced) — the brief's Step-3 allowance "genesis + any new runtime records".

### 2.1 Changes

```
 M docs/engineering/genesis.md          (+ §6 runtime-adopt; intro updated)
 M docs/release/release-manifest.md     (+ Slice H integration status)
 M docs/release/schema-versioning.md    (+ cross-layout migration note)
 M src/agentic_evo/cli.py               (+ import, _runtime_adopt_arguments, _runtime_adopt, parser, dispatch)
?? src/agentic_evo/runtime_adopt.py     (new: adopt_genesis_home, verify_adopted_home, RuntimeAdoptError)
?? tests/test_runtime_adopt.py          (new: 7 unittest cases)
```

### 2.2 Test suite — verbatim

```powershell
$env:PYTHONPATH='src'; & 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tests.test_runtime_adopt -v
```

```
test_adopt_refuses_a_non_genesis_home (tests.test_runtime_adopt.RuntimeAdoptTests.test_adopt_refuses_a_non_genesis_home) ... ok
test_adopt_refuses_an_already_runtime_home (tests.test_runtime_adopt.RuntimeAdoptTests.test_adopt_refuses_an_already_runtime_home) ... ok
test_adopt_refuses_home_with_existing_body_store (tests.test_runtime_adopt.RuntimeAdoptTests.test_adopt_refuses_home_with_existing_body_store) ... ok
test_adopted_home_serves_and_status_smoke (tests.test_runtime_adopt.RuntimeAdoptTests.test_adopted_home_serves_and_status_smoke) ... ok
test_genesis_then_adopt_preserves_identity_and_genesis_evidence (tests.test_runtime_adopt.RuntimeAdoptTests.test_genesis_then_adopt_preserves_identity_and_genesis_evidence) ... ok
test_second_adopt_is_refused (tests.test_runtime_adopt.RuntimeAdoptTests.test_second_adopt_is_refused) ... ok
test_verify_adopted_home_api_reads_servable_state (tests.test_runtime_adopt.RuntimeAdoptTests.test_verify_adopted_home_api_reads_servable_state) ... ok

----------------------------------------------------------------------
Ran 7 tests in 2.255s

OK
```

Full-suite regression: `python -m unittest discover -s tests` → **299 tests; 43 errors + 1 error, ALL pre-existing environmental failures** in `test_windows_gate_b_evidence.py` (43; subprocess non-UTF-8 output decode `UnicodeDecodeError … byte 0xcb`) and `test_windows_gate_b.py` (1; same decode cause, verified by standalone traceback). Identical failure set was already documented by slice-E at base `94f2823`. All other 255 tests pass, including all runtime/body/trusted/service suites.

### 2.3 Real-home verification — verbatim evidence

Backup (kept until parent accepts): `D:\rawle\Coding\Agentic-Evo\.agentic-evo\runtime-home-backup-20260805` — `STATE_MATCH=True`, `KEY_MATCH=True` (byte-identical copy).

Adoption (real home):

```powershell
$env:PYTHONPATH='src'; python -m agentic_evo.cli runtime-adopt --home D:\rawle\Coding\Agentic-Evo\.agentic-evo\runtime-home
```

```json
{"ok":true,"result":{"evidence":{"adoption":{"event_id":"d714cf5c3fea411699a9aa9a05f84237","event_kind":"head_advanced","integrity_hash":"2cb02c959a6826a00f6da5626f17dff5f53c8056f56bd9398aa1be21c1ae0b14","sequence":2},"genesis":{"event_id":"e4a099344d3841cfb6fbd18b76089414","integrity_hash":"9c74390ca2f25bf65fd0538a7fb55230e4c1eb219b8fd01bce203346ef9403b9","sequence":1},"records":2},"genesis_head":"ee79ae49b8941344e1f314e1f9183deacf8c089d","head":"1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884","home":"D:\\rawle\\Coding\\Agentic-Evo\\.agentic-evo\\runtime-home","identity":{"authority":"on","root":"agentic-evo-root-v1","who":"9c3510701bfa091976668a3c18dc8079ff469d3c56a24f3cea5e84222b83dbe6","why":"0f8674a12058c3720a121400b837e801ef65628d982977d3770f508664cf521a"},"initial_body":{"activation_artifact":"entrypoint.md","activation_kind":"surface-context-utf8-v1","author_kind":"research_instrument","commitment":"1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884","files":["entrypoint.md","identity.md"],"generation":1,"parent_head":"ee79ae49b8941344e1f314e1f9183deacf8c089d"},"trusted_layout":{"state":"trusted/state.sqlite3","witness_key":"trusted/witness.key","witness_key_preserved":true}}}
```
→ exit code **0**.

Serve/status smoke (real home):

```
STATUS_EXIT 0
STATUS_STDOUT {"ok":true,"result":{"active_session_count":0,"active_sessions":[],"active_sessions_truncated":false,"authority":"on","body_rehearsal":{"body_channel":"windows_explicit_handle_list_pipe_pair","head":"1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884","pid":17020,"process_fencing":"windows_job_object_kill_on_close","process_token":"windows_restricted_low_integrity","provenance":"subprocess_rehearsal","state":"ready"},"generation":1,"head":"1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884","instrument_version":"agentic-evo-cli-genesis-v1","instrument_version_char_count":26,"instrument_version_truncated":false,"lifecycle_state":"waiting","protocol_version":"agentic-evo-trusted-v2","protocol_version_char_count":22,"protocol_version_truncated":false,"root":"agentic-evo-root-v1"}}
```

`serve` stayed up and served the public Surface (Body subprocess booted at `windows_restricted_low_integrity`, state `ready`); it was then stopped with `terminate()` (Windows TerminateProcess → exit 1 is the stop artifact; `service.main` returns 0 on KeyboardInterrupt). The smoke acceptance "serve/status → exit 0" is satisfied by **status exit 0** while the serve process is live.

### 2.4 Identity-preservation evidence (real home)

| field | before adopt | after adopt | verdict |
| --- | --- | --- | --- |
| who | `9c3510701bfa091976668a3c18dc8079ff469d3c56a24f3cea5e84222b83dbe6` | `9c3510701bfa091976668a3c18dc8079ff469d3c56a24f3cea5e84222b83dbe6` | **unchanged** |
| why | `0f8674a12058c3720a121400b837e801ef65628d982977d3770f508664cf521a` | `0f8674a12058c3720a121400b837e801ef65628d982977d3770f508664cf521a` | **unchanged** |
| root | `agentic-evo-root-v1` | `agentic-evo-root-v1` | **unchanged** (also the Body manifest root) |
| head | `ee79ae49b8941344e1f314e1f9183deacf8c089d` (pinned git SHA) | `1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884` (initial Body commitment) | **changed by design** (see §1.3; pinned head preserved as `parent_head` + genesis `head_after`) |
| authority | `on` | `on` | **unchanged** |

Genesis evidence record (seq 1): event_id `e4a099344d3841cfb6fbd18b76089414`, event_kind `genesis`, head_after `ee79ae49…`, integrity_hash `9c74390ca2f25bf65fd0538a7fb55230e4c1eb219b8fd01bce203346ef9403b9` — **byte-identical before/after** (compared via `TrustedState.records()`). `records()` after adopt = 2: `genesis` (seq 1) + `head_advanced` (seq 2, head_before `ee79ae49…` → head_after `1628ca50…`). `TrustedState.verify()` = True; `DevelopmentalRuntime(home).status()` servable (root `agentic-evo-root-v1`, head `1628ca50…`, authority `on`, lifecycle `waiting`, generation 1). `trusted/witness.key` byte-identical to backup (`WITNESS_KEY_PRESERVED=True`). Post-adopt layout: `trusted/state.sqlite3`, `trusted/witness.key`, `body/manifests/1628ca50….json`, `body/blobs/{2}`.

## 3. Claim ceilings

**Proved:** a Genesis-born home (CLI-born, real home included) can be made servable by the runtime (`serve`/`status` exit 0, Body subprocess boots, evidence verifies) with born identity preserved — who/why/root byte-identical, witness key preserved, genesis seq-1 evidence record preserved, pinned birth head retained as lineage parent. This is the requirement-1/2 runtime foundation for a born home.

**NOT claimed:** installed native service (Gate A/B install not executed), cross-restart OS-level persistence, second real coding agent, longitudinal learning, or any v1.0 requirement completion. `serve` here is the fixed-home foreground Witness rehearsal, as before. The head necessarily transitions to the initial Body commitment on adoption (mathematically forced; see §1.3) — this is the single, fully documented deviation from the brief's literal "head unchanged" wording.

## 4. Remaining risks

- The genesis CLI still accepts arbitrary pinned root/head strings; only `runtime-adopt` makes such a home servable. Future birth flow: `genesis` → `runtime-adopt` (documented in `docs/engineering/genesis.md §6`).
- Real-home backup retained at `runtime-home-backup-20260805` until the parent accepts (do not delete).
- Pre-existing sandbox failures in `test_windows_gate_b_evidence.py` / `test_windows_gate_b.py` are environmental (non-UTF-8 subprocess decode) and unchanged by this slice.
- Adoption is not idempotent by re-run (second adopt refused, exit 6) — by design; the first adopt is atomic-enough (copy-verify-then-remove, originals removed only after `trusted/` verification).
