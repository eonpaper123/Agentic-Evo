from __future__ import annotations

"""Dry-run pack export + artifact verification against the REAL born home.

Read-only with respect to the runtime home: loads the runtime, validates the
committed prereg, exports the pack over the genesis-anchored window (seq 2),
verifies the artifact, and writes ONLY the pack JSON under experiments/001/ in
this worktree. stdlib only; no network; no lifecycle/service/UAC/restart.
"""

import hashlib
import json
import os
from pathlib import Path
import sys

from agentic_evo.errors import IntegrityError
from agentic_evo.experiment_pack import (
    _validate_prereg,
    export_experiment_pack,
    verify_experiment_artifact,
)
from agentic_evo.runtime import DevelopmentalRuntime

HOME = Path(os.environ["AGENTIC_EVO_EXPERIMENT_HOME"])
PREREG = Path("experiments") / "001" / "prereg.json"
PACK_ARTIFACT = Path("experiments") / "001" / "pack_dry_run.json"

EXPECTED_ROOT_STRING = "agentic-evo-root-v1"
EXPECTED_HEAD = "1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884"
EXPECTED_GENESIS_SEQUENCE = 1
EXPECTED_GENESIS_HASH = (
    "9c74390ca2f25bf65fd0538a7fb55230e4c1eb219b8fd01bce203346ef9403b9"
)
EXPECTED_ROOT_COMMITMENT = (
    "c78860b7a6dc65c6af61f69a6e54f4b5c86f9b8b5d612019a7159c95daabe497"
)


def _write_preserving_order(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))

    runtime = DevelopmentalRuntime.load(HOME)
    status = runtime.status()
    records = runtime.evidence.records()

    home_checks = {
        "root_string": status.root == EXPECTED_ROOT_STRING,
        "head": status.head == EXPECTED_HEAD,
        "records_count": len(records) == 2,
        "genesis_sequence": records[0].sequence == EXPECTED_GENESIS_SEQUENCE,
        "genesis_integrity_hash": records[0].integrity_hash == EXPECTED_GENESIS_HASH,
        "genesis_event_kind": records[0].event_kind == "genesis",
        "tail_event_kind": records[-1].event_kind == "head_advanced",
        "tail_head_after_is_current_head": records[-1].head_after == status.head,
        "genesis_head_differs_from_current_head": (
            records[0].head_after != status.head
        ),
    }
    print(json.dumps({"home_checks": home_checks}, ensure_ascii=False))
    if not all(home_checks.values()):
        print(json.dumps({"fatal": "born home does not match the pinned identity"}, ensure_ascii=False))
        return 2

    try:
        _validate_prereg(prereg, status=status)
        prereg_status = "valid"
    except IntegrityError as exc:
        prereg_status = f"rejected: {exc}"
        print(json.dumps({"prereg_validation": prereg_status}, ensure_ascii=False))
        return 2
    print(json.dumps({"prereg_validation": prereg_status}, ensure_ascii=False))

    end_sequence = records[-1].sequence
    try:
        pack = export_experiment_pack(runtime, prereg, end_sequence=end_sequence)
        pack_export = "accepted"
    except IntegrityError as exc:
        pack_export = f"rejected: {exc}"
        print(json.dumps({"pack_export": pack_export}, ensure_ascii=False))
        return 2
    print(json.dumps({"pack_export": pack_export}, ensure_ascii=False))

    try:
        result = verify_experiment_artifact(pack)
        artifact_verification = result
    except IntegrityError as exc:
        artifact_verification = {"error": str(exc)}
        print(json.dumps({"artifact_verification": artifact_verification}, ensure_ascii=False))
        return 2
    print(json.dumps({"artifact_verification": artifact_verification}, ensure_ascii=False))

    _write_preserving_order(PACK_ARTIFACT, pack)
    artifact_bytes = PACK_ARTIFACT.read_bytes()
    print(
        json.dumps(
            {
                "pack_artifact_path": str(PACK_ARTIFACT),
                "pack_artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "pack_artifact_bytes": len(artifact_bytes),
                "end_sequence": end_sequence,
            },
            ensure_ascii=False,
        )
    )
    print(json.dumps({"pack_summary": {
        "end_anchor": pack["end_anchor"],
        "head_end": pack["head_end"],
        "authority_end": pack["authority_end"],
        "evidence_window_sequences": [r["sequence"] for r in pack["evidence_window"]],
        "body_manifest_heads": [e["head"] for e in pack["body_manifests"]],
    }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
