"""Detached exports for longitudinal experiment evidence."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from ._util import canonical_json_bytes, sha256_hex
from .body import BODY_SCHEMA_VERSION
from .errors import IntegrityError
from .evidence import EVIDENCE_SCHEMA_VERSION


ALLOWED_HYPOTHESIS_REFS = ("H001-A", "H001-B", "H001-C", "H001-D", "H001-E", "H001-F")
ALLOWED_CONTROL_REFS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")
EXPERIMENT_PREREG_SCHEMA = "agentic-evo.experiment-prereg.v1"
EXPERIMENT_PACK_SCHEMA = "agentic-evo.experiment-pack.v1"
EXPERIMENT_VERIFY_SCHEMA = "agentic-evo.experiment-artifact-verification.v1"
EXPERIMENT_PROTOCOL_REF = "experiments/001_机器级连续生命循环.md"
EXPERIMENT_CLAIM_CEILING = {
    "artifact_integrity": "established",
    "committed_head_lineage": "established",
    "declared_metadata_truth": "not_established",
    "causal_attribution": "not_established",
    "future_capability_gain": "not_established",
    "memory_formation": "not_established",
    "autonomous_learning": "not_established",
    "self_evolution": "not_established",
    "second_real_coding_agent_natural_usage": "not_established",
    "zero_human_learning_intervention": "not_established",
}

_PREREG_FIELDS = frozenset({
    "schema", "protocol_ref", "instrument_version", "protocol_version",
    "root_commitment", "head_start", "execution_surface", "project_environment",
    "hypothesis_refs", "control_refs", "start_anchor", "claim_ceiling",
})
_PACK_FIELDS = frozenset({
    "schema", "prereg", "end_anchor", "head_end", "authority_end",
    "evidence_window", "body_manifests", "claim_ceiling",
})
_RECORD_FIELDS = frozenset({
    "schema_version", "event_id", "sequence", "instrument_version",
    "protocol_version", "event_kind", "occurred_at", "observed_at",
    "root_commitment", "head_before", "head_after", "source_kind",
    "author_kind", "execution_surface", "session_id", "turn_id",
    "tool_call_id", "project_environment", "correlation_ref", "causation_ref",
    "parent_ref", "human_intervention_kind", "coverage_gap", "payload",
    "previous_integrity_hash", "integrity_hash",
})
_MANIFEST_FIELDS = frozenset({
    "schema_version", "root", "parent_head", "generation", "author_kind",
    "created_at", "activation_kind", "activation_artifact",
    "development_kind", "development_artifact", "files",
})
_HASH = re.compile(r"^[0-9a-f]{64}$")


class _ExperimentArtifactConsistencyError(IntegrityError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise _ExperimentArtifactConsistencyError(code)


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _canonical_root_commitment(root: Any) -> str:
    """Return the canonical 64-hex root commitment for a root value.

    A 64-hex root (``TrustedState.generate_root``) commits to itself; a born
    home's named root (e.g. ``agentic-evo-root-v1``) commits to its sha256.
    The same canonicalization is applied to the prereg, to every evidence
    record's ``root_commitment`` and to every Body manifest's ``root`` so a
    born+adopted home validates against its prereg.
    """
    if _is_hash(root):
        return root
    if not isinstance(root, str) or not root:
        raise IntegrityError("invalid root commitment value")
    return sha256_hex(root)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _ceiling_is_exact(value: Any) -> bool:
    return isinstance(value, dict) and list(value.items()) == list(EXPERIMENT_CLAIM_CEILING.items())


def _anchor_is_valid(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"sequence", "integrity_hash"}
        and _is_sequence(value["sequence"])
        and _is_hash(value["integrity_hash"])
    )


def _validate_prereg(prereg: Any, *, status: Any | None = None) -> dict[str, Any]:
    if not isinstance(prereg, dict) or set(prereg) != _PREREG_FIELDS:
        _fail("invalid_prereg")
    if prereg.get("schema") != EXPERIMENT_PREREG_SCHEMA or prereg.get("protocol_ref") != EXPERIMENT_PROTOCOL_REF:
        _fail("invalid_prereg")
    if prereg.get("hypothesis_refs") != list(ALLOWED_HYPOTHESIS_REFS) or prereg.get("control_refs") != list(ALLOWED_CONTROL_REFS):
        _fail("invalid_prereg")
    if not _ceiling_is_exact(prereg.get("claim_ceiling")) or not _anchor_is_valid(prereg.get("start_anchor")):
        _fail("invalid_prereg")
    for field in ("instrument_version", "protocol_version"):
        if not isinstance(prereg.get(field), str) or not prereg[field]:
            _fail("invalid_prereg")
    if not _is_hash(prereg.get("root_commitment")) or not _is_hash(prereg.get("head_start")):
        _fail("invalid_prereg")
    for field in ("execution_surface", "project_environment"):
        if prereg.get(field) is not None and not isinstance(prereg[field], str):
            _fail("invalid_prereg")
    if status is not None and (
        prereg["root_commitment"] != _canonical_root_commitment(status.root)
        or prereg["instrument_version"] != status.instrument_version
        or prereg["protocol_version"] != status.protocol_version
    ):
        _fail("invalid_prereg")
    return prereg


def export_experiment_prereg(
    runtime: Any,
    *,
    hypothesis_refs: list[str],
    control_refs: list[str],
) -> dict[str, Any]:
    if hypothesis_refs != list(ALLOWED_HYPOTHESIS_REFS) or control_refs != list(ALLOWED_CONTROL_REFS):
        _fail("invalid_prereg")
    status = runtime.status()
    tail = runtime.evidence.records()[-1]
    return {
        "schema": EXPERIMENT_PREREG_SCHEMA,
        "protocol_ref": EXPERIMENT_PROTOCOL_REF,
        "instrument_version": status.instrument_version,
        "protocol_version": status.protocol_version,
        "root_commitment": _canonical_root_commitment(status.root),
        "head_start": status.head,
        "execution_surface": tail.execution_surface,
        "project_environment": tail.project_environment,
        "hypothesis_refs": list(ALLOWED_HYPOTHESIS_REFS),
        "control_refs": list(ALLOWED_CONTROL_REFS),
        "start_anchor": {"sequence": tail.sequence, "integrity_hash": tail.integrity_hash},
        "claim_ceiling": dict(EXPERIMENT_CLAIM_CEILING),
    }


def _checkpoint_authority(db_path: Path, sequence: int) -> str:
    try:
        connection = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT record_json FROM checkpoints WHERE sequence = ?", (sequence,)
            ).fetchone()
        finally:
            connection.close()
        checkpoint = json.loads(row[0]) if row is not None else None
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise IntegrityError("cannot read committed checkpoint authority") from exc
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("authority"), str):
        raise IntegrityError("invalid committed checkpoint authority")
    return checkpoint["authority"]


def _raw_manifest(runtime: Any, head: str) -> dict[str, Any]:
    try:
        value = json.loads((runtime.body_store.manifest_path / f"{head}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntegrityError("cannot read body manifest") from exc
    if not isinstance(value, dict):
        raise IntegrityError("invalid body manifest")
    return value


def _manifest_entries(runtime: Any, prereg: Mapping[str, Any], window: list[dict[str, Any]]) -> list[dict[str, Any]]:
    heads = [prereg["head_start"]]
    for record in window:
        # Deduplicate: in a born+adopted lineage the first window record (the
        # adoption head_advanced) advances TO head_start itself, so the same
        # Body commitment must not appear twice in the manifest chain.
        if record["head_after"] != record["head_before"] and record["head_after"] != heads[-1]:
            heads.append(record["head_after"])
    entries = [{"head": head, "manifest": _raw_manifest(runtime, head)} for head in heads]
    _verify_manifests(entries, prereg, window[-1]["head_after"])
    return entries


def export_experiment_pack(
    runtime: Any,
    prereg: Mapping[str, Any],
    *,
    end_sequence: int | None = None,
) -> dict[str, Any]:
    status = runtime.status()
    prereg = _validate_prereg(dict(prereg) if isinstance(prereg, Mapping) else prereg, status=status)
    records = [asdict(record) for record in runtime.evidence.records()]
    start = prereg["start_anchor"]
    anchor = next((record for record in records if record["sequence"] == start["sequence"]), None)
    if anchor is None or anchor["integrity_hash"] != start["integrity_hash"]:
        _fail("invalid_prereg")
    tail = records[-1]["sequence"]
    end = tail if end_sequence is None else end_sequence
    if not _is_sequence(end) or end <= start["sequence"]:
        _fail("empty_evidence_window")
    if end > tail:
        _fail("evidence_window_inconsistent")
    window = [record for record in records if start["sequence"] < record["sequence"] <= end]
    if not window or len(window) != end - start["sequence"]:
        _fail("evidence_window_inconsistent")
    if window[0]["head_before"] != anchor["head_after"]:
        _fail("evidence_window_inconsistent")
    if _is_hash(anchor["head_after"]):
        if prereg["head_start"] != anchor["head_after"]:
            _fail("invalid_prereg")
    else:
        # Born+adopted lineage: the start anchor is the Genesis evidence whose
        # head_after is the pinned (non-Body) birth head, so head_start cannot
        # equal it; head_start must instead be the Body head the first
        # head-changing window record (the runtime-adopt head_advanced)
        # advances TO.
        first_change = next(
            (record for record in window if record["head_after"] != record["head_before"]),
            None,
        )
        if first_change is None or prereg["head_start"] != first_change["head_after"]:
            _fail("invalid_prereg")
    end_record = window[-1]
    body_manifests = _manifest_entries(runtime, prereg, window)
    return {
        "schema": EXPERIMENT_PACK_SCHEMA,
        "prereg": prereg,
        "end_anchor": {"sequence": end_record["sequence"], "integrity_hash": end_record["integrity_hash"]},
        "head_end": end_record["head_after"],
        "authority_end": _checkpoint_authority(runtime.trusted.db_path, end),
        "evidence_window": window,
        "body_manifests": body_manifests,
        "claim_ceiling": dict(EXPERIMENT_CLAIM_CEILING),
    }


def _verify_window(prereg: Mapping[str, Any], window: Any, end_anchor: Any, head_end: Any) -> list[dict[str, Any]]:
    if not isinstance(window, list) or not window:
        _fail("empty_evidence_window")
    if not _anchor_is_valid(end_anchor) or not _is_hash(head_end):
        _fail("evidence_window_inconsistent")
    previous_hash = prereg["start_anchor"]["integrity_hash"]
    previous_head = prereg["head_start"]
    expected_sequence = prereg["start_anchor"]["sequence"] + 1
    checked: list[dict[str, Any]] = []
    for index, record in enumerate(window):
        if not isinstance(record, dict) or set(record) != _RECORD_FIELDS:
            _fail("evidence_window_inconsistent")
        head_before_matches = record.get("head_before") == previous_head
        # Born+adopted lineage: the start anchor may be the Genesis evidence
        # whose head_after is the pinned (non-Body) birth head, while head_start
        # is the adopted Body head the first window record advances TO; then
        # head_before != head_start is legitimate after runtime-adopt.
        if index == 0 and not head_before_matches:
            head_before_matches = record.get("head_after") == prereg["head_start"]
        if (
            record.get("schema_version") != EVIDENCE_SCHEMA_VERSION
            or record.get("sequence") != expected_sequence
            or record.get("previous_integrity_hash") != previous_hash
            or _canonical_root_commitment(record.get("root_commitment")) != prereg["root_commitment"]
            or record.get("instrument_version") != prereg["instrument_version"]
            or record.get("protocol_version") != prereg["protocol_version"]
            or not head_before_matches
        ):
            _fail("evidence_window_inconsistent")
        claimed = record.get("integrity_hash")
        unsigned = dict(record)
        unsigned.pop("integrity_hash")
        if not _is_hash(claimed) or sha256_hex(canonical_json_bytes(unsigned)) != claimed:
            _fail("evidence_window_inconsistent")
        previous_hash, previous_head, expected_sequence = (
            claimed,
            record["head_after"],
            expected_sequence + 1,
        )
        checked.append(record)
    tail = checked[-1]
    if end_anchor != {"sequence": tail["sequence"], "integrity_hash": tail["integrity_hash"]} or head_end != tail["head_after"]:
        _fail("evidence_window_inconsistent")
    return checked


def _verify_manifests(entries: Any, prereg: Mapping[str, Any], head_end: str) -> None:
    if not isinstance(entries, list) or not entries:
        _fail("body_manifest_chain_inconsistent")
    expected_heads = [prereg["head_start"]]
    # The caller supplies the window-derived endpoint separately; its chain is checked below.
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {"head", "manifest"} or not _is_hash(entry.get("head")):
            _fail("body_manifest_chain_inconsistent")
        manifest = entry["manifest"]
        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
            _fail("body_manifest_chain_inconsistent")
        if (
            manifest.get("schema_version") != BODY_SCHEMA_VERSION
            or _canonical_root_commitment(manifest.get("root")) != prereg["root_commitment"]
            or sha256_hex(canonical_json_bytes(manifest)) != entry["head"]
        ):
            _fail("body_manifest_chain_inconsistent")
        if index and manifest.get("parent_head") != entries[index - 1]["head"]:
            _fail("body_manifest_chain_inconsistent")
    if entries[0]["head"] != expected_heads[0] or entries[-1]["head"] != head_end:
        _fail("body_manifest_chain_inconsistent")


def verify_experiment_artifact(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict) or artifact.get("schema") != EXPERIMENT_PACK_SCHEMA:
        _fail("unsupported_schema")
    if set(artifact) != _PACK_FIELDS:
        _fail("unsupported_schema")
    prereg = _validate_prereg(artifact.get("prereg"))
    if not _ceiling_is_exact(artifact.get("claim_ceiling")):
        _fail("claim_ceiling_changed")
    window = _verify_window(prereg, artifact.get("evidence_window"), artifact.get("end_anchor"), artifact.get("head_end"))
    if not isinstance(artifact.get("authority_end"), str) or not artifact["authority_end"]:
        _fail("evidence_window_inconsistent")
    expected_heads = [prereg["head_start"]]
    for record in window:
        if record["head_after"] != record["head_before"] and record["head_after"] != expected_heads[-1]:
            expected_heads.append(record["head_after"])
    entries = artifact.get("body_manifests")
    _verify_manifests(entries, prereg, artifact["head_end"])
    if [entry["head"] for entry in entries] != expected_heads:
        _fail("body_manifest_chain_inconsistent")
    return {"schema": EXPERIMENT_VERIFY_SCHEMA, "valid": True}


__all__ = ["export_experiment_prereg", "export_experiment_pack", "verify_experiment_artifact"]
