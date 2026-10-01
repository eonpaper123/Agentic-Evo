from __future__ import annotations

"""Export and validate the experiment-001 prereg against the REAL born home.

Read-only with respect to the runtime home: loads the runtime, reads status and
evidence records, exports the prereg, applies the task-pinned anchors, and runs
the module's own prereg validation. Writes only the canonical artifact JSON
under experiments/001/ in this worktree. stdlib only; no network.
Run from the repository root with PYTHONPATH=src and set
AGENTIC_EVO_EXPERIMENT_HOME to the original born home plus
AGENTIC_EVO_EXPERIMENT_PROJECT to the exact project_environment in the
committed prereg.json. A new checkout path is not an equivalent anchor.
"""

import hashlib
import json
import os
from pathlib import Path
import sys

from agentic_evo._util import sha256_hex
from agentic_evo.errors import IntegrityError
from agentic_evo.experiment_pack import (
    ALLOWED_CONTROL_REFS,
    ALLOWED_HYPOTHESIS_REFS,
    _validate_prereg,
    export_experiment_pack,
    export_experiment_prereg,
)
from agentic_evo.runtime import DevelopmentalRuntime

HOME = Path(os.environ["AGENTIC_EVO_EXPERIMENT_HOME"])
ARTIFACT = Path("experiments") / "001" / "prereg.json"

EXPECTED_ROOT_STRING = "agentic-evo-root-v1"
EXPECTED_HEAD = "1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884"
EXPECTED_GENESIS_SEQUENCE = 1
EXPECTED_GENESIS_HASH = (
    "9c74390ca2f25bf65fd0538a7fb55230e4c1eb219b8fd01bce203346ef9403b9"
)
EXECUTION_SURFACE = "opencode"
PROJECT_ENVIRONMENT = os.environ["AGENTIC_EVO_EXPERIMENT_PROJECT"]


def _record_summary(record: object) -> dict[str, object]:
    return {
        "sequence": record.sequence,
        "event_id": record.event_id,
        "event_kind": record.event_kind,
        "integrity_hash": record.integrity_hash,
        "head_before": record.head_before,
        "head_after": record.head_after,
        "execution_surface": record.execution_surface,
        "project_environment": record.project_environment,
        "source_kind": record.source_kind,
        "author_kind": record.author_kind,
    }


def _write_preserving_order(path: Path, value: dict[str, object]) -> None:
    """Write JSON preserving dict insertion order.

    The module's claim-ceiling validation compares key ORDER against
    EXPERIMENT_CLAIM_CEILING, so a sort-keys serialization would not survive a
    load-and-validate round trip. Human-readable indented JSON keeps the
    canonical (module) order intact.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    runtime = DevelopmentalRuntime.load(HOME)
    status = runtime.status()
    records = runtime.evidence.records()

    checks = {
        "root_string": status.root == EXPECTED_ROOT_STRING,
        "head": status.head == EXPECTED_HEAD,
        "records_count": len(records) == 2,
        "genesis_sequence": records[0].sequence == EXPECTED_GENESIS_SEQUENCE,
        "genesis_integrity_hash": records[0].integrity_hash == EXPECTED_GENESIS_HASH,
        "genesis_event_kind": records[0].event_kind == "genesis",
        "tail_event_kind": records[-1].event_kind == "head_advanced",
    }
    print(json.dumps({"home_checks": checks}, ensure_ascii=False))
    if not all(checks.values()):
        print(json.dumps({"fatal": "born home does not match the pinned identity"}, ensure_ascii=False))
        return 2

    status_payload = {
        "root": status.root,
        "head": status.head,
        "generation": status.generation,
        "authority": status.authority,
        "lifecycle_state": status.lifecycle_state,
        "instrument_version": status.instrument_version,
        "protocol_version": status.protocol_version,
    }
    print(json.dumps({"status": status_payload}, ensure_ascii=False))
    print(json.dumps({"evidence_records": [_record_summary(r) for r in records]}, ensure_ascii=False))

    baseline = export_experiment_prereg(
        runtime,
        hypothesis_refs=list(ALLOWED_HYPOTHESIS_REFS),
        control_refs=list(ALLOWED_CONTROL_REFS),
    )
    print(json.dumps({"baseline_prereg_from_code": baseline}, ensure_ascii=False))

    root_commitment = sha256_hex(EXPECTED_ROOT_STRING)
    print(json.dumps({"root_commitment_sha256_of_born_root_string": root_commitment}, ensure_ascii=False))

    final = dict(baseline)
    final["root_commitment"] = root_commitment
    final["execution_surface"] = EXECUTION_SURFACE
    final["project_environment"] = PROJECT_ENVIRONMENT
    final["start_anchor"] = {
        "sequence": EXPECTED_GENESIS_SEQUENCE,
        "integrity_hash": EXPECTED_GENESIS_HASH,
    }

    # The module's own prereg validation (raises IntegrityError on failure).
    _validate_prereg(final)

    # Probe the pack exporter against this prereg/home (read-only).  Since
    # Slice I the module canonicalizes the born (non-hex) root and accepts the
    # born+adopted lineage, so this probe must be accepted (exit 0).
    pack_probe = "not_run"
    try:
        export_experiment_pack(runtime, final, end_sequence=2)
        pack_probe = "accepted"
    except IntegrityError as exc:
        pack_probe = f"rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 - probe only, report verbatim
        pack_probe = f"rejected: {type(exc).__name__}: {exc}"
    print(json.dumps({"pack_export_probe": pack_probe}, ensure_ascii=False))

    _write_preserving_order(ARTIFACT, final)
    artifact_bytes = ARTIFACT.read_bytes()
    print(
        json.dumps(
            {
                "artifact_path": str(ARTIFACT),
                "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "artifact_bytes": len(artifact_bytes),
            },
            ensure_ascii=False,
        )
    )
    print(json.dumps({"final_prereg": final}, ensure_ascii=False))

    # Read back and validate the on-disk artifact with the module validator.
    reloaded = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    _validate_prereg(reloaded)
    print(json.dumps({"artifact_reload_validation": "valid"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
