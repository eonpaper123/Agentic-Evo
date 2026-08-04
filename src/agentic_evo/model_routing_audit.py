"""Explicit, local model-route decision records with no provider side effect.

The audit records an agent-authored declaration.  It never calls a provider,
changes model configuration, or turns a surface-supplied model label into an
observation of provider-effective routing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Sequence
from uuid import uuid4

from ._util import ExclusiveFileLock, atomic_write_json, canonical_json_bytes, sha256_hex
from .evidence_provenance import (
    EvidenceProvenanceError,
    ensure_ordinary_relative_directory,
    reject_secret_shaped,
    relative_text,
    require_ordinary_file,
    resolve_project_path,
    validate_provenance_path,
    validate_record_provenance,
)
from .test_result_receipts import (
    TestResultReceiptError,
    verify_test_result_receipt_file,
)


MODEL_ROUTE_SCHEMA = "agentic-evo.model-routing-decision.v1"
MODEL_ROUTE_PRODUCER = "agentic-evo.model-routing-audit.v1"
MODEL_ROUTE_NAMESPACE = "artifacts/p0-p1/agent-authored/model-routing"
_ROUTE_CLAIM_CEILING = "declared route decision; provider execution not observed"
_DECISION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BASES = {"declared_policy", "cost", "availability", "manual_override"}
_RECORD_KEYS = (
    "schema",
    "decision_id",
    "project",
    "provenance",
    "request",
    "candidates",
    "selection",
    "evidence_refs",
    "observed_effect",
    "integrity_sha256",
)


class ModelRoutingAuditError(ValueError):
    """Raised when a route decision record is unsafe, invalid, or tampered."""


def _from_provenance(error: EvidenceProvenanceError) -> ModelRoutingAuditError:
    return ModelRoutingAuditError(str(error))


def _closed_object(value: object, keys: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ModelRoutingAuditError(f"{label} is not a closed object")
    return value


def _decision_id(value: object) -> str:
    if not isinstance(value, str) or not _DECISION_ID_RE.fullmatch(value):
        raise ModelRoutingAuditError("decision_id must be 32 lowercase hexadecimal characters")
    return value


def _text(value: object, label: str, *, maximum_bytes: int = 240) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ModelRoutingAuditError(f"{label} must be a bounded non-empty single-line string")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ModelRoutingAuditError(f"{label} exceeds the byte bound")
    try:
        reject_secret_shaped(value, field=label)
    except EvidenceProvenanceError as error:
        raise _from_provenance(error) from error
    return value


def _digest(value: dict[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("integrity_sha256", None)
    return sha256_hex(canonical_json_bytes(unsigned))


def _policy_reference(project_root: str | Path, policy_ref: object) -> str:
    try:
        relative = relative_text(policy_ref, field="policy_ref")
        if "@" in relative:
            raise EvidenceProvenanceError("policy_ref cannot contain @")
        _, target = resolve_project_path(project_root, relative, field="policy_ref")
        require_ordinary_file(target)
        digest = sha256_hex(target.read_bytes())
    except (EvidenceProvenanceError, OSError) as error:
        raise ModelRoutingAuditError("policy_ref must name an ordinary project-relative file") from error
    return f"{relative}@{digest}"


def _validate_policy_reference(project_root: str | Path, value: object) -> str:
    if not isinstance(value, str) or value.count("@") != 1:
        raise ModelRoutingAuditError("selection policy_ref must be path@sha256")
    relative, digest = value.split("@", 1)
    if not relative or not _SHA256_RE.fullmatch(digest):
        raise ModelRoutingAuditError("selection policy_ref must be path@sha256")
    if _policy_reference(project_root, relative) != value:
        raise ModelRoutingAuditError("selection policy_ref digest does not match its current file")
    return value


def _verify_evidence_refs(project_root: str | Path, value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ModelRoutingAuditError("evidence_refs must be an array")
    checked: list[dict[str, Any]] = []
    paths: set[str] = set()
    for item in value:
        reference = _closed_object(item, ("receipt_sha256", "relative_path"), "evidence reference")
        receipt_sha256 = reference["receipt_sha256"]
        if not isinstance(receipt_sha256, str) or not _SHA256_RE.fullmatch(receipt_sha256):
            raise ModelRoutingAuditError("evidence reference receipt_sha256 is invalid")
        try:
            relative_path = relative_text(reference["relative_path"], field="evidence reference relative_path")
        except EvidenceProvenanceError as error:
            raise _from_provenance(error) from error
        if relative_path in paths:
            raise ModelRoutingAuditError("evidence reference paths must be unique")
        try:
            receipt = verify_test_result_receipt_file(project_root, relative_path)
        except TestResultReceiptError as error:
            raise ModelRoutingAuditError("evidence reference is not an approved observed-local receipt") from error
        if receipt["integrity_sha256"] != receipt_sha256:
            raise ModelRoutingAuditError("evidence reference receipt digest does not match")
        paths.add(relative_path)
        checked.append({"receipt_sha256": receipt_sha256, "relative_path": relative_path})
    return checked


def validate_model_routing_decision(
    value: object,
    *,
    project_root: str | Path,
) -> dict[str, Any]:
    """Validate a record and every local reference without a provider call."""

    record = _closed_object(value, _RECORD_KEYS, "model routing decision")
    if record["schema"] != MODEL_ROUTE_SCHEMA or record["project"] != "agentic-evo":
        raise ModelRoutingAuditError("model routing schema or project is unsupported")
    _decision_id(record["decision_id"])
    try:
        validate_record_provenance(
            record["provenance"],
            provenance_class="agent_authored_decision",
            producer=MODEL_ROUTE_PRODUCER,
            claim_ceiling=_ROUTE_CLAIM_CEILING,
        )
    except EvidenceProvenanceError as error:
        raise _from_provenance(error) from error

    request = _closed_object(record["request"], ("capability",), "route request")
    _text(request["capability"], "request capability")
    candidates = record["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ModelRoutingAuditError("candidates must be a non-empty array")
    checked_candidates = [_text(candidate, "candidate model ref") for candidate in candidates]
    if len(set(checked_candidates)) != len(checked_candidates):
        raise ModelRoutingAuditError("candidates must not contain duplicates")

    selection = _closed_object(
        record["selection"],
        ("selected_model_ref", "basis", "policy_ref", "fallback_model_ref"),
        "route selection",
    )
    selected = _text(selection["selected_model_ref"], "selection selected_model_ref")
    if selected not in checked_candidates:
        raise ModelRoutingAuditError("selection selected_model_ref must be a candidate")
    if selection["basis"] not in _BASES:
        raise ModelRoutingAuditError("selection basis is unsupported")
    _validate_policy_reference(project_root, selection["policy_ref"])
    fallback = selection["fallback_model_ref"]
    if fallback is not None:
        fallback = _text(fallback, "selection fallback_model_ref")
        if fallback not in checked_candidates or fallback == selected:
            raise ModelRoutingAuditError("selection fallback_model_ref must be another candidate or null")

    _verify_evidence_refs(project_root, record["evidence_refs"])
    observed_effect = _closed_object(
        record["observed_effect"],
        ("request_executed", "provider_effective_model"),
        "observed effect",
    )
    if observed_effect["request_executed"] is not False:
        raise ModelRoutingAuditError("route audit cannot claim that a request executed")
    if observed_effect["provider_effective_model"] != "not_observed":
        raise ModelRoutingAuditError("route audit must retain provider effective model as not_observed")

    integrity = record["integrity_sha256"]
    if not isinstance(integrity, str) or not _SHA256_RE.fullmatch(integrity):
        raise ModelRoutingAuditError("record integrity hash is invalid")
    if integrity != _digest(record):
        raise ModelRoutingAuditError("record integrity hash does not match")
    return record


def _record_directory(project_root: str | Path, record_dir: str | Path) -> tuple[Path, Path, str]:
    try:
        relative = relative_text(record_dir, field="record_dir")
        target = validate_provenance_path(
            project_root,
            "agent_authored_decision",
            relative,
        )
    except EvidenceProvenanceError as error:
        raise _from_provenance(error) from error
    parts = Path(relative).parts
    expected = Path(MODEL_ROUTE_NAMESPACE).parts
    if len(parts) != len(expected) + 1:
        raise ModelRoutingAuditError("record_dir must be directly below the model-routing namespace")
    decision_id = parts[-1]
    _decision_id(decision_id)
    return Path(project_root).absolute(), target, decision_id


def verify_model_routing_decision(
    project_root: str | Path,
    record_dir: str | Path,
) -> dict[str, Any]:
    """Verify one closed agent-authored record and its local evidence bindings."""

    root, directory, decision_id = _record_directory(project_root, record_dir)
    try:
        if not directory.is_dir() or directory.is_symlink():
            raise ModelRoutingAuditError("record directory is absent or unsafe")
        entries = list(directory.iterdir())
    except OSError as error:
        raise ModelRoutingAuditError("cannot inspect record directory") from error
    if len(entries) != 1 or entries[0].name != "record.json":
        raise ModelRoutingAuditError("record directory must contain exactly record.json")
    try:
        require_ordinary_file(entries[0])
        raw = entries[0].read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (EvidenceProvenanceError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelRoutingAuditError("cannot read a safe record.json") from error
    record = validate_model_routing_decision(value, project_root=root)
    if raw != canonical_json_bytes(record) + b"\n":
        raise ModelRoutingAuditError("record.json is not canonical JSON with a trailing newline")
    if record["decision_id"] != decision_id:
        raise ModelRoutingAuditError("decision_id does not match the publication directory")
    return record


def _publication_namespace(project_root: str | Path, namespace: str | Path) -> tuple[Path, str]:
    try:
        relative = relative_text(namespace, field="record_namespace")
        validate_provenance_path(
            project_root,
            "agent_authored_decision",
            relative,
            require_exact_root=True,
        )
    except EvidenceProvenanceError as error:
        raise _from_provenance(error) from error
    if relative != MODEL_ROUTE_NAMESPACE:
        raise ModelRoutingAuditError("record_namespace must be the exact model-routing namespace")
    return Path(project_root).absolute(), relative


def record_model_routing_decision(
    *,
    project_root: str | Path,
    record_namespace: str | Path,
    decision_id: str,
    capability: str,
    candidate_model_refs: Sequence[str],
    selected_model_ref: str,
    basis: str,
    policy_ref: str | Path,
    evidence_refs: Sequence[str | Path] = (),
    fallback_model_ref: str | None = None,
) -> dict[str, Any]:
    """Publish one agent-authored route decision through an atomic directory rename."""

    root, namespace_relative = _publication_namespace(project_root, record_namespace)
    _decision_id(decision_id)
    checked_capability = _text(capability, "request capability")
    checked_candidates = [_text(item, "candidate model ref") for item in candidate_model_refs]
    if not checked_candidates or len(set(checked_candidates)) != len(checked_candidates):
        raise ModelRoutingAuditError("candidates must be non-empty and unique")
    checked_selected = _text(selected_model_ref, "selection selected_model_ref")
    if checked_selected not in checked_candidates:
        raise ModelRoutingAuditError("selection selected_model_ref must be a candidate")
    if basis not in _BASES:
        raise ModelRoutingAuditError("selection basis is unsupported")
    checked_fallback: str | None = None
    if fallback_model_ref is not None:
        checked_fallback = _text(fallback_model_ref, "selection fallback_model_ref")
        if checked_fallback not in checked_candidates or checked_fallback == checked_selected:
            raise ModelRoutingAuditError("selection fallback_model_ref must be another candidate or null")
    checked_policy_ref = _policy_reference(root, policy_ref)
    prepared_refs: list[dict[str, Any]] = []
    for evidence_ref in evidence_refs:
        try:
            relative = relative_text(evidence_ref, field="evidence_ref")
            receipt = verify_test_result_receipt_file(root, relative)
        except (EvidenceProvenanceError, TestResultReceiptError) as error:
            raise ModelRoutingAuditError("evidence_ref is not an approved observed-local receipt") from error
        prepared_refs.append(
            {"receipt_sha256": receipt["integrity_sha256"], "relative_path": relative}
        )
    # Revalidate unique paths and current receipt integrity before publication.
    _verify_evidence_refs(root, prepared_refs)

    try:
        namespace = ensure_ordinary_relative_directory(root, namespace_relative)
    except EvidenceProvenanceError as error:
        raise _from_provenance(error) from error
    final_directory = namespace / decision_id
    lock_path = namespace / ".model-routing-publication.lock"
    with ExclusiveFileLock(lock_path):
        if final_directory.exists() or final_directory.is_symlink():
            raise ModelRoutingAuditError("decision_id directory already exists")
        staging = namespace / f".staging-{decision_id}-{uuid4().hex}"
        try:
            staging.mkdir(mode=0o700)
        except OSError as error:
            raise ModelRoutingAuditError("cannot create record staging directory") from error
        record: dict[str, Any] = {
            "schema": MODEL_ROUTE_SCHEMA,
            "decision_id": decision_id,
            "project": "agentic-evo",
            "provenance": {
                "class": "agent_authored_decision",
                "producer": MODEL_ROUTE_PRODUCER,
                "authorization_ref": None,
                "claim_ceiling": [_ROUTE_CLAIM_CEILING],
            },
            "request": {"capability": checked_capability},
            "candidates": checked_candidates,
            "selection": {
                "selected_model_ref": checked_selected,
                "basis": basis,
                "policy_ref": checked_policy_ref,
                "fallback_model_ref": checked_fallback,
            },
            "evidence_refs": prepared_refs,
            "observed_effect": {
                "request_executed": False,
                "provider_effective_model": "not_observed",
            },
        }
        record["integrity_sha256"] = _digest(record)
        validate_model_routing_decision(record, project_root=root)
        try:
            atomic_write_json(staging / "record.json", record)
            raw = (staging / "record.json").read_bytes()
            if raw != canonical_json_bytes(record) + b"\n":
                raise ModelRoutingAuditError("staged record bytes are not canonical")
            validate_model_routing_decision(json.loads(raw.decode("utf-8")), project_root=root)
            os.replace(staging, final_directory)
        except BaseException:
            if staging.exists():
                try:
                    for child in staging.iterdir():
                        child.unlink()
                    staging.rmdir()
                except OSError:
                    pass
            raise
    return record


record_model_route = record_model_routing_decision
verify_model_route = verify_model_routing_decision
