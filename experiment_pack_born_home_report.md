# Slice I report — experiment_pack validation against the born home

Worktree: `<AGENTIC_EVO_REPO>\.lingtai\agentic-evo-total\work\dev-integrate` (branch `agent/v1.0-integration`, base d7854e6)
Date: 2026-08-05
Scope: only this worktree; nothing staged/committed; shared checkout untouched; real home `<AGENTIC_EVO_REPO>\.agentic-evo\runtime-home` opened read-only (verified byte-identical before/after). stdlib only, no network, no lifecycle/service/UAC/restart.

## 1. Canonical semantics decided

1. **root_commitment canonicalization.** `_canonical_root_commitment(root)` returns the root itself when it is a 64-hex string, else `sha256_hex(root)`. The SAME function is applied in every root comparison: prereg export (`root_commitment`), prereg-vs-status validation, evidence-record `root_commitment` (records carry the raw born string `agentic-evo-root-v1`), and Body-manifest `root` (manifests carry the raw born string too). For the real home: `sha256_hex("agentic-evo-root-v1") = c78860b7a6dc65c6af61f69a6e54f4b5c86f9b8b5d612019a7159c95daabe497`, matching the committed prereg. This is NOT a weakening: every layer still must commit to exactly one canonical root, and the prereg/record/manifest values must agree through the same rule.
2. **Born+adopted lineage anchors.** The committed prereg anchors at the Genesis evidence record (`start_anchor = {sequence: 1, integrity_hash: 9c74390c…}`) while `head_start = 1628ca50…` is the adopted Body head; the Genesis record's `head_after = ee79ae49…` is a pinned 40-hex (non-Body) birth head. After runtime-adopt it is legitimate that `genesis.head_after != head_start` and `genesis.head_after != current head`. The pack's window runs `(start_anchor.sequence, end_anchor.sequence]` with `end_anchor` = latest evidence (seq 2, `2cb02c95…`), and the head lineage is validated as follows:
   - the evidence hash chain starts from `start_anchor.integrity_hash` and each window record's `previous_integrity_hash` must chain to it (unchanged);
   - `window[0].head_before` must equal `anchor.head_after` (export validates this against the live home);
   - `head_start` binding: when `anchor.head_after` is a Body commitment, `head_start == anchor.head_after` (unchanged strict rule); when the anchor head is the pinned non-Body birth head, `head_start` must equal the `head_after` of the first head-changing window record — the runtime-adopt `head_advanced` (seq 2) — which is exactly the real home's shape;
   - inside the window, each record's `head_before` must equal the previous record's `head_after`; for the first window record the detached verifier accepts `head_after == head_start` as the born+adopted alternative (the artifact cannot see the anchor record itself; export does check `window[0].head_before == anchor.head_after` against the home);
   - the Body manifest chain is the ordered, deduplicated list of heads from `head_start` to `head_end` (the adoption advances TO `head_start`, so the same commitment must not appear twice; heads strictly advance, so dedup changes nothing for ordinary lineages).

## 2. Changes in `src/agentic_evo/experiment_pack.py` (minimal, honest)

| # | Change | Rationale |
|---|---|---|
| 1 | Add `_canonical_root_commitment` | Single canonical rule for root comparisons (semantics §1.1). |
| 2 | `export_experiment_prereg`: emit `_canonical_root_commitment(status.root)` | Born home export no longer emits the raw non-hex root. |
| 3 | `_validate_prereg(status=…)`: compare against `_canonical_root_commitment(status.root)` | Committed prereg (`c78860b7…`) now validates against the born home. |
| 4 | `export_experiment_pack`: replace `anchor.head_after == head_start` with the two-branch binding + require `window[0].head_before == anchor.head_after` | Accepts born+adopted lineage; keeps the strict rule when the anchor head is a Body commitment. |
| 5 | `_manifest_entries`: deduplicate consecutive identical heads | Adoption advances TO `head_start`; the pack must not carry the same manifest twice. |
| 6 | `_verify_window`: canonical root compare + first-record born+adopted `head_after == head_start` alternative | Detached artifact verification of the same lineage model. |
| 7 | `_verify_manifests`: canonical root compare | Manifest `root` is the raw born string. |
| 8 | `verify_experiment_artifact`: deduplicate expected manifest heads | Mirrors change 5 for detached verification. |

No integrity check was removed: the committed prereg anchors, the evidence hash chain, sequence contiguity, end-anchor/head-end equality, manifest self-hashes and parent chain, claim-ceiling exactness, authority_end and schema exactness all remain enforced. Two checks were made lineage-aware exactly as the design brief requires.

## 3. Tests

New: `tests/test_experiment_pack_born_home.py` (8 tests) — fixture home born exactly like the Genesis CLI births the real home (named root, pinned 40-hex genesis head) then `runtime-adopt`ed; covers canonical root export, genesis-anchored prereg validation, pack export, artifact verification, tamper rejection (wrong head_start, unknown anchor, root/evidence/head tampering, foreign manifest root), and the tail-anchored prereg path.

Experiment suite (new + existing):

```
Ran 20 tests in 0.782s
OK
```

Full suite, before vs after:

```
before: Ran 299 tests in 78.611s — FAILED (errors=43)
after : Ran 307 tests in 78.224s — FAILED (errors=43)
failure sets identical: True  (new-only: []  gone: [])
```

The 43 errors are the pre-existing gate-B environment failures: 1 in `test_windows_gate_b`, 42 in `test_windows_gate_b_evidence` (fixture setup fails on a missing external anchor/SID environment value before any assertion). No non-gate-B test failed before or after. Raw outputs: `.scratch_slice_i/baseline_suite.txt`, `.scratch_slice_i/after_suite.txt`.

## 4. Real-home verification (read-only on the home)

`experiments/001/pack_born_home_verify.py` (stdlib, PYTHONPATH=src, repo runtime python 3.12.13) — loads the real home, validates the committed prereg, dry-run exports the pack, verifies the artifact, writes ONLY `experiments/001/pack_dry_run.json` (sha256 `09ddc9e08c7cad26bc7a48d5292069e75372f0a9802fa2d7a27e9b28eb53fe4d`, 4547 bytes). Verbatim output + exit code:

```json
{"home_checks": {"root_string": true, "head": true, "records_count": true, "genesis_sequence": true, "genesis_integrity_hash": true, "genesis_event_kind": true, "tail_event_kind": true, "tail_head_after_is_current_head": true, "genesis_head_differs_from_current_head": true}}
{"prereg_validation": "valid"}
{"pack_export": "accepted"}
{"artifact_verification": {"schema": "agentic-evo.experiment-artifact-verification.v1", "valid": true}}
{"pack_artifact_path": "experiments\\001\\pack_dry_run.json", "pack_artifact_sha256": "09ddc9e08c7cad26bc7a48d5292069e75372f0a9802fa2d7a27e9b28eb53fe4d", "pack_artifact_bytes": 4547, "end_sequence": 2}
{"pack_summary": {"end_anchor": {"sequence": 2, "integrity_hash": "2cb02c959a6826a00f6da5626f17dff5f53c8056f56bd9398aa1be21c1ae0b14"}, "head_end": "1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884", "authority_end": "on", "evidence_window_sequences": [2], "body_manifest_heads": ["1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884"]}}
EXIT_CODE= 0
```

Re-run of the original export script (`experiments/001/prereg_export.py`) now reports the probe as accepted and the prereg artifact re-export is idempotent:

```json
{"pack_export_probe": "accepted"}
{"artifact_path": "experiments\\001\\prereg.json", "artifact_sha256": "5ebc3245bb50b739ae2410625f6e991c1ec739f135b46cb875edb77bd8319511", "artifact_bytes": 1389}
{"artifact_reload_validation": "valid"}
EXIT_CODE= 0
```

Home read-only proof: per-file sha256/size/mtime snapshot of every file under the home taken before any Slice-I home access and after all verification runs — `keys_equal: True`, `diff_count: 0` (`.scratch_slice_i/home_snapshot_before.json` vs `home_snapshot_after.json`). The pack JSON is the verbatim exported artifact (schema `agentic-evo.experiment-pack.v1`); its `body_manifests` is the single adopted Body commitment `1628ca50…` with `parent_head = ee79ae49…` (the pinned genesis head), which is the honest chain for the window [seq 2].

## 5. Docs updated

- `experiments/001_机器级连续生命循环.md` §17.1 item 6: recorded pack blocker replaced with the Slice-I resolution and the accepted/valid evidence.
- `experiment_001_report.md`: §5 probe note and §7 item 2 blocker paragraph marked resolved by Slice I.
- `experiments/001/prereg_export.py`: stale "expected to be blocked" probe comment updated to reflect the new accepted semantics.
- `experiments/001/pack_born_home_verify.py` + `experiments/001/pack_dry_run.json`: new rerunnable dry-run evidence (the pack artifact is the deliverable of STEP 3).

## 6. Claim ceilings (unchanged)

Identical to the prereg/§17.2: `artifact_integrity` and `committed_head_lineage` = `established` (internal consistency only); all learning/evolution/Gate-B/installation/second-agent claims remain `not_established`. This slice establishes no new capability, memory, learning or evolution evidence.

## 7. Files changed (worktree only, nothing staged/committed)

- `src/agentic_evo/experiment_pack.py` (modified)
- `tests/test_experiment_pack_born_home.py` (new)
- `experiments/001/pack_born_home_verify.py` (new), `experiments/001/pack_dry_run.json` (new)
- `experiments/001_机器级连续生命循环.md`, `experiment_001_report.md`, `experiments/001/prereg_export.py` (modified)
- `.scratch_slice_i/` (raw evidence: ground-truth dump, home snapshots, suite logs)

## 8. Remaining risks

- The detached `verify_experiment_artifact` cannot see the anchor record itself; the born+adopted alternative for the first window record (`head_after == head_start`) is the internal-consistency rule, while `export_experiment_pack` additionally checks `window[0].head_before == anchor.head_after` against the live home. Trust anchors remain the committed prereg (start_anchor hash) and the self-hashed manifests.
- A prereg anchored at Genesis whose `head_start` is a later Body head (experiment advanced the head again before registration) is conservatively rejected (first head-changing window record must reach `head_start`); the code-derived tail-anchored prereg remains the supported path for that shape.
- The 43 gate-B environment failures are pre-existing (identical sets before/after) and out of scope.
