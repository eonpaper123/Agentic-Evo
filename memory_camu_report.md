# Slice B report — Memory-to-Capability mechanism space (CAMU store)

Worktree: `<AGENTIC_EVO_REPO>\.lingtai\agentic-evo-total\work\dev-memory`
(branch `agent/memory-capability`, base `1f886b0`). Nothing was staged or committed.
Spec: `../slice_b_memory_design.md` (followed strictly).

## 1. Files created / changed

| Path | Status | Notes |
| --- | --- | --- |
| `src/agentic_evo/memory_store.py` | **new** | Body-owned CAMU store: append-only hash-chained JSONL, ⟨G,A,I,P,E⟩ contract, content addressing, verify_chain, add/get/list/count, record_use, record_outcome, recall (pluggable evaluator), consolidate scaffold. Pure stdlib + `.errors` only. |
| `src/agentic_evo/errors.py` | changed (+8) | Added `MemoryIntegrityError`, `MemoryRecordError` (existing style). |
| `src/agentic_evo/cli.py` | changed (+274) | Added 7 subcommands: memory-camu-add/list/show/outcome, memory-recall, memory-consolidate, memory-verify-chain. |
| `tests/test_memory_camu.py` | **new** | 32 unittest cases: contract validation, tamper detection, grounding refs, append-only use_log, outcome bookkeeping, recall, consolidate, caller-owned import boundary. |
| `docs/engineering/memory-capability.md` | **new** | Chinese engineering doc (mechanism space, CAMU contract, boundary, CLI usage, claim ceilings). |
| `memory_camu_report.md` | **new** | This file. |

`git status --porcelain` (verbatim):

```
 M src/agentic_evo/cli.py
 M src/agentic_evo/errors.py
?? src/agentic_evo/memory_store.py
?? tests/test_memory_camu.py
```

No other existing file was modified; nothing in `trusted/`, `body/`, `kernel/`, `runtime/`,
`witness/` or the pre-existing tests was touched.

## 2. Verbatim test output and exit codes

### 2.1 Required unit-test command (repo runtime Python)

Interpreter verified first: `C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
→ `Python 3.12.13` (matches GOAL.md runtime path).

Command:

```powershell
$env:PYTHONPATH='src'; & 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tests.test_memory_camu -v
```

Output (verbatim):

```
test_caller_owned_import_boundary (tests.test_memory_camu.MemoryStoreBoundaryTests.test_caller_owned_import_boundary) ... ok
test_consolidate_accepts_iso_and_datetime_now (tests.test_memory_camu.MemoryStoreConsolidateTests.test_consolidate_accepts_iso_and_datetime_now) ... ok
test_consolidate_marks_overdue_without_deleting (tests.test_memory_camu.MemoryStoreConsolidateTests.test_consolidate_marks_overdue_without_deleting) ... ok
test_consolidate_overdue_survives_reload_and_chain (tests.test_memory_camu.MemoryStoreConsolidateTests.test_consolidate_overdue_survives_reload_and_chain) ... ok
test_consolidate_skips_recent_and_non_pending_with_default_ttl (tests.test_memory_camu.MemoryStoreConsolidateTests.test_consolidate_skips_recent_and_non_pending_with_default_ttl) ... ok
test_add_rejects_unknown_top_level_fields (tests.test_memory_camu.MemoryStoreContractTests.test_add_rejects_unknown_top_level_fields) ... ok
test_add_requires_every_contract_field (tests.test_memory_camu.MemoryStoreContractTests.test_add_requires_every_contract_field) ... ok
test_add_requires_required_subfields (tests.test_memory_camu.MemoryStoreContractTests.test_add_requires_required_subfields) ... ok
test_bad_evidence_refs_shape_rejected (tests.test_memory_camu.MemoryStoreContractTests.test_bad_evidence_refs_shape_rejected) ... ok
test_content_address_is_deterministic (tests.test_memory_camu.MemoryStoreContractTests.test_content_address_is_deterministic) ... ok
test_create_existing_store_raises (tests.test_memory_camu.MemoryStoreContractTests.test_create_existing_store_raises) ... ok
test_duplicate_content_address_rejected (tests.test_memory_camu.MemoryStoreContractTests.test_duplicate_content_address_rejected) ... ok
test_get_unknown_id_raises (tests.test_memory_camu.MemoryStoreContractTests.test_get_unknown_id_raises) ... ok
test_hash_chain_integrity (tests.test_memory_camu.MemoryStoreContractTests.test_hash_chain_integrity) ... ok
test_load_missing_store_raises (tests.test_memory_camu.MemoryStoreContractTests.test_load_missing_store_raises) ... ok
test_non_pending_initial_status_rejected (tests.test_memory_camu.MemoryStoreContractTests.test_non_pending_initial_status_rejected) ... ok
test_tamper_detection_byte_flip (tests.test_memory_camu.MemoryStoreContractTests.test_tamper_detection_byte_flip) ... ok
test_tamper_detection_structured_edit (tests.test_memory_camu.MemoryStoreContractTests.test_tamper_detection_structured_edit) ... ok
test_tampered_history_cannot_be_extended (tests.test_memory_camu.MemoryStoreContractTests.test_tampered_history_cannot_be_extended) ... ok
test_unknown_influence_domain_rejected (tests.test_memory_camu.MemoryStoreContractTests.test_unknown_influence_domain_rejected) ... ok
test_valid_evidence_refs_accepted_and_preserved (tests.test_memory_camu.MemoryStoreContractTests.test_valid_evidence_refs_accepted_and_preserved) ... ok
test_custom_evaluator_is_pluggable (tests.test_memory_camu.MemoryStoreRecallTests.test_custom_evaluator_is_pluggable) ... ok
test_default_evaluator_is_a_shallow_predicate_matcher (tests.test_memory_camu.MemoryStoreRecallTests.test_default_evaluator_is_a_shallow_predicate_matcher) ... ok
test_default_evaluator_matches_and_non_matches (tests.test_memory_camu.MemoryStoreRecallTests.test_default_evaluator_matches_and_non_matches) ... ok
test_recall_evaluator_failure_is_surfaced (tests.test_memory_camu.MemoryStoreRecallTests.test_recall_evaluator_failure_is_surfaced) ... ok
test_record_outcome_flips_pending_to_contradicted (tests.test_memory_camu.MemoryStoreUseOutcomeTests.test_record_outcome_flips_pending_to_contradicted) ... ok
test_record_outcome_flips_pending_to_verified (tests.test_memory_camu.MemoryStoreUseOutcomeTests.test_record_outcome_flips_pending_to_verified) ... ok
test_record_outcome_mixed_evidence_returns_to_pending (tests.test_memory_camu.MemoryStoreUseOutcomeTests.test_record_outcome_mixed_evidence_returns_to_pending) ... ok
test_record_outcome_requires_bool_matched (tests.test_memory_camu.MemoryStoreUseOutcomeTests.test_record_outcome_requires_bool_matched) ... ok
test_record_use_appends_and_never_rewrites (tests.test_memory_camu.MemoryStoreUseOutcomeTests.test_record_use_appends_and_never_rewrites) ... ok
test_record_use_unknown_id_raises (tests.test_memory_camu.MemoryStoreUseOutcomeTests.test_record_use_unknown_id_raises) ... ok
test_statuses_persist_across_reload (tests.test_memory_camu.MemoryStoreUseOutcomeTests.test_statuses_persist_across_reload) ... ok

----------------------------------------------------------------------
Ran 32 tests in 0.540s

OK
```

Exit code: **0**.

### 2.2 py_compile with `-S`

Command:

```powershell
$env:PYTHONPATH='src'; & 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -S -m py_compile src/agentic_evo/memory_store.py src/agentic_evo/errors.py src/agentic_evo/cli.py tests/test_memory_camu.py
```

Output: `PYCOMPILE_EXIT=0` (no output, no errors). Exit code: **0**.

### 2.3 CLI smoke (all 7 subcommands)

Fixture: two CAMUs added to a temp store via `memory-camu-add --record-file`
(interpreter + `PYTHONPATH=src` as above). Verbatim:

```
{"ok":true,"result":{"id":"73bfe3a4e9f512a07a30220e95ba97db825830213813b193594756b01aee31ea"}}    ADD1_EXIT=0
{"ok":true,"result":{"id":"76b0b755c6f71e5045c5d601c3cd535be6096284cffe546779b93543d12b8c35"}}    ADD2_EXIT=0
memory-camu-list  -> {"ok":true,"result":{"camus":[{"condition":"compile_error observed","id":"73bf...","influence_domain":"tool_selection","prediction_status":"pending","recorded_at":"..."},{"condition":"test_fail observed","id":"76b0...","influence_domain":"predict","prediction_status":"pending","recorded_at":"..."}],"count":2}}    LIST_EXIT=0
memory-camu-show  -> {"ok":true,"result":{"id":"73bf...","record":{...full effective CAMU...},"recorded_at":"...","sequence":1}}    SHOW_EXIT=0
memory-camu-outcome --matched true -> {"ok":true,"result":{"id":"73bf...","prediction_status":"verified"}}    OUTCOME_EXIT=0
memory-recall (matching context)   -> {"ok":true,"result":{"matches":["73bf..."]}}    RECALL_EXIT=0
memory-recall (non-matching)       -> {"ok":true,"result":{"matches":[]}}    RECALL_NOMATCH_EXIT=0
memory-consolidate --ttl-days 0    -> {"ok":true,"result":{"overdue":1,"scanned":2}}    CONSOLIDATE_EXIT=0
memory-verify-chain (intact store) -> {"ok":true,"result":{"records":2,"verified":true}}    VERIFY_EXIT=0
--- tamper one byte in the store, then ---
memory-verify-chain                -> stderr: {"ok":false,"result":{"message":"store record integrity mismatch","verified":false}}    TAMPER_VERIFY_EXIT=7
memory-camu-add (bad record)       -> stderr: {"error":{"code":"memory_error","message":"store record integrity mismatch"},"ok":false}    BAD_ADD_EXIT=6
```

(BAD_ADD hit the tampered store first, so it surfaced the integrity error; the
contract rejection itself — unknown influence domain — is covered by the unit
tests `test_unknown_influence_domain_rejected`.) CLI exit-code convention:
`0` success, `6` memory_error, `7` verify-chain failure.

### 2.4 Full regression suite

`python -m unittest discover -s tests -p 'test_*.py'` was run twice:

- Full discovery: **failures only in `test_windows_gate_b` and
  `test_windows_gate_b_evidence`** — these modules invoke Windows SCM probes
  (`sc.exe`-style native commands); in this sandbox the native commands return
  no usable output (`NoneType`), and their stdout is GBK-encoded so subprocess
  UTF-8 decoding fails. Pre-existing environment limitation; those modules were
  **not touched** by this slice.
- The same suite minus those two environment-dependent modules:
  `Ran 225 tests in 44.351s ... OK` — exit code **0**, failures=0, errors=0.
  (This includes the 32 new memory tests.)

## 3. Claim ceilings (honest)

**Established by this slice:**

- The CAMU mechanism space exists, is verifiable (append-only hash chain, content
  addressing, contract validation) and is Body-owned (caller-owned module, pure
  stdlib + `errors`, imports none of trusted/body/kernel/runtime/witness — enforced
  by an AST + namespace test).
- `record_use` is append-only; `record_outcome` applies the status-bookkeeping rule
  (all-support→verified, all-oppose→contradicted, mixed→pending); `consolidate` marks
  pending+older-than-TTL as `overdue` and never deletes.
- The recall evaluator is a pluggable hook; the default is an explicit placeholder.

**NOT claimed:** memory formation; causal capability gain; learning; self-evolution;
that any recall mechanism is "the" memory system; that consolidation is a forgetting
policy; any real-task integration (smoke tests only, later slice); cross-process
strong consistency under concurrency.

## 4. Design decisions

1. **Single append-only JSONL with a chained event model.** The file's first line is a
   meta header; each CAMU is a `camu` line (content-addressed); epistemic changes are
   `camu_update` lines (`use` / `outcome` / `status`) chained into the same hash chain.
   `get`/`list` replay base + updates into the *effective* record. This satisfies
   "append-only" + "never rewrites history" + tamper detection without in-place
   mutation (which would break the chain).
2. **Content address** = `sha256(canonical_json(⟨G,A,I,P,E⟩))` over the normalized record;
   deterministic across stores; duplicate addresses rejected.
3. **record_outcome bookkeeping**: outcome entries accumulate in `E.support`/`E.oppose`
   (replayed); the status rule is recomputed and a `status` update is appended only when
   it changes. An `overdue` status is replaced by fresh outcome evidence (documented).
4. **consolidate** ages by `recorded_at` (when the CAMU was added to the store) against
   `now − ttl_seconds`; `now` accepts an ISO string, a `datetime`, or None (wall clock).
5. **Cross-process safety**: the `_StoreLock` mirrors the repo's msvcrt/fcntl lock
   pattern but is implemented locally so the module stays self-contained; a sidecar
   lock file `.<store>.lock` is created (documented side artifact).
6. **CLI**: JSON-lines I/O consistent with existing subcommands; `--store` defaults to
   `memory/camus.jsonl`; exit codes 0/6/7 as documented.
7. **Strict contract validation** (unknown top-level fields rejected, status must be
   `pending` on creation, evidence_refs must be `{sequence:int>=1, hash:64-hex}`) keeps
   the address space deterministic.

## 5. Remaining risks

- **Concurrency**: appends happen under a cross-process lock, but the append itself
  relies on O_APPEND atomicity for small lines; multi-writer contention is untested.
- **Integrity is a hash chain, not a signature**: a writer who can rewrite the whole file
  and recompute hashes is undetectable. By design the store is declarative and outside
  the TCB (no HMAC), so verify_chain detects accidental corruption/tampering, not a
  malicious rewrite.
- **record_outcome vs overdue**: an outcome recorded after consolidation flips `overdue`
  back to verified/contradicted. If consolidation should be sticky, revisit later.
- **Tooling note**: the daemon's `file write` action was non-functional during this run
  ("unsupported file input field" for any input); new files were written as UTF-8
  without BOM via shell/.NET and the `edit` action. All artifacts should be reviewed as
  UTF-8.
- **Windows-native SCM tests** (`test_windows_gate_b*`) cannot run in this sandbox
  (native probes unavailable, GBK stdout). Pre-existing; unrelated to this slice.
