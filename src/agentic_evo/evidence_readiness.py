"""Strict project-local P0/P1 evidence-readiness documents.

The canonical JSON remains the authoritative readiness state.  Markdown is a
stable rendering of that JSON only; checking it never writes either file.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from ._util import canonical_json_bytes, sha256_hex


class EvidenceReadinessError(ValueError):
    """Raised when a P0/P1 readiness or disposition document is invalid."""


READINESS_SCHEMA = "agentic-evo.evidence-readiness.v1"
P2_STOP_SCHEMA = "agentic-evo.p2-stop-decision.v1"
PROJECT = "agentic-evo"
SCOPE = "p0-p1-project-local-pre-genesis"
P2_SCOPE = "agentic-evo repository/P2 only"

_TOP_LEVEL_KEYS = (
    "schema",
    "project",
    "scope",
    "baseline_commit",
    "overall_status",
    "claim_ceiling",
    "provenance_classes",
    "lanes",
    "blocked_by",
    "non_claims",
)
_LANE_KEYS = (
    "id",
    "title",
    "owner_role",
    "status",
    "scope",
    "inputs",
    "deliverables",
    "exit_criteria",
    "evidence_classes",
    "authorization",
    "non_claims",
)
_EXIT_CRITERION_KEYS = ("description", "fulfilled")
_CLAIM_CEILING = {
    "gate_a_complete": False,
    "gate_b_outcome": "not_established",
    "native_security_verified": False,
    "ready_to_install": False,
}
_PROVENANCE_CLASSES = (
    "synthetic_fixture",
    "observed_local_test",
    "agent_authored_decision",
    "external_authorized_observation",
)
_LANE_STATUSES = {
    "not_started",
    "planned",
    "in_progress",
    "blocked",
    "complete",
    "stopped",
}
_AUTHORIZATIONS = {"none", "Rawle_explicit", "external_resource"}
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

_P2_KEYS = (
    "schema",
    "project",
    "scope",
    "owner_role",
    "decision",
    "status",
    "baseline_commit",
    "basis_refs",
    "blocked_by",
    "reopen_conditions",
    "non_actions",
    "does_not_apply_to",
    "record_integrity_sha256",
)
_REQUIRED_P2_BASIS_REFS = {
    "docs/engineering/evidence-readiness.v1.json#L1",
    "docs/engineering/evidence-readiness.v1.json#L2",
    "docs/engineering/evidence-readiness.v1.json#L3",
}
_REQUIRED_P2_NON_ACTIONS = {
    "treating this record as consent",
    "scheduling work",
    "changing a live goal",
    "invoking a provider",
    "running a Windows retained experiment",
}
_REQUIRED_P2_NON_APPLICABILITY = {
    "global Rawle rules",
    "other repositories",
    "model-provider configuration",
    "authorization grants",
}


def _closed_object(value: object, keys: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value) != keys:
        raise EvidenceReadinessError(f"{label} keys or key order are not canonical")
    return value


def _bounded_text(value: object, label: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise EvidenceReadinessError(f"{label} must be a bounded non-empty string")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise EvidenceReadinessError(f"{label} must be a non-empty array")
    return [_bounded_text(item, f"{label} item") for item in value]


def _validate_exit_criteria(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise EvidenceReadinessError("lane exit_criteria must be a non-empty array")
    criteria: list[dict[str, Any]] = []
    for item in value:
        criterion = _closed_object(item, _EXIT_CRITERION_KEYS, "exit criterion")
        _bounded_text(criterion["description"], "exit criterion description")
        if not isinstance(criterion["fulfilled"], bool):
            raise EvidenceReadinessError("exit criterion fulfilled must be a boolean")
        criteria.append(criterion)
    return criteria


def validate_matrix(value: object) -> dict[str, Any]:
    """Validate the closed readiness matrix without reading or writing files."""

    matrix = _closed_object(value, _TOP_LEVEL_KEYS, "readiness matrix")
    if matrix["schema"] != READINESS_SCHEMA:
        raise EvidenceReadinessError("readiness schema is unsupported")
    if matrix["project"] != PROJECT or matrix["scope"] != SCOPE:
        raise EvidenceReadinessError("readiness project or scope is unsupported")
    if not isinstance(matrix["baseline_commit"], str) or not _COMMIT_RE.fullmatch(
        matrix["baseline_commit"]
    ):
        raise EvidenceReadinessError("baseline_commit must be 40 lowercase hexadecimal characters")
    if matrix["overall_status"] not in {"not_ready", "ready"}:
        raise EvidenceReadinessError("overall_status is unsupported")
    if matrix["claim_ceiling"] != _CLAIM_CEILING:
        raise EvidenceReadinessError("the four existing Gate claim fields changed")
    if matrix["provenance_classes"] != list(_PROVENANCE_CLASSES):
        raise EvidenceReadinessError("provenance_classes changed")

    lanes = matrix["lanes"]
    if not isinstance(lanes, list) or len(lanes) != 4:
        raise EvidenceReadinessError("lanes must contain exactly four ordered lanes")
    for position, raw_lane in enumerate(lanes, start=1):
        lane = _closed_object(raw_lane, _LANE_KEYS, "lane")
        if lane["id"] != f"L{position}":
            raise EvidenceReadinessError("lanes must be ordered L1 through L4")
        _bounded_text(lane["title"], "lane title")
        _bounded_text(lane["owner_role"], "lane owner_role")
        _bounded_text(lane["scope"], "lane scope")
        if lane["status"] not in _LANE_STATUSES:
            raise EvidenceReadinessError("lane status is unsupported")
        if lane["authorization"] not in _AUTHORIZATIONS:
            raise EvidenceReadinessError("lane authorization is unsupported")
        _string_list(lane["inputs"], "lane inputs")
        _string_list(lane["deliverables"], "lane deliverables")
        criteria = _validate_exit_criteria(lane["exit_criteria"])
        evidence_classes = _string_list(lane["evidence_classes"], "lane evidence_classes")
        if any(item not in _PROVENANCE_CLASSES for item in evidence_classes):
            raise EvidenceReadinessError("lane has an unsupported evidence class")
        _string_list(lane["non_claims"], "lane non_claims")
        if lane["status"] == "complete" and any(
            not criterion["fulfilled"] for criterion in criteria
        ):
            raise EvidenceReadinessError("a complete lane has an unfulfilled exit criterion")

    blocked_by = matrix["blocked_by"]
    if not isinstance(blocked_by, list):
        raise EvidenceReadinessError("blocked_by must be an array")
    if matrix["overall_status"] == "ready":
        if blocked_by:
            raise EvidenceReadinessError("a ready matrix must have no blocked_by entries")
        if any(lane["status"] != "complete" for lane in lanes):
            raise EvidenceReadinessError("a ready matrix requires every lane complete")
    else:
        _string_list(blocked_by, "blocked_by")
    _string_list(matrix["non_claims"], "non_claims")
    return matrix


def canonical_matrix_bytes(value: object) -> bytes:
    matrix = validate_matrix(value)
    return json.dumps(matrix, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def load_matrix(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        raw = target.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceReadinessError("cannot read a valid readiness matrix") from error
    matrix = validate_matrix(value)
    if raw != canonical_matrix_bytes(matrix):
        raise EvidenceReadinessError("readiness matrix is not canonical pretty UTF-8 JSON")
    return matrix


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def render_markdown(value: object) -> str:
    """Render stable UTF-8 Markdown from an already validated matrix."""

    matrix = validate_matrix(value)
    lines = [
        "# P0/P1 evidence readiness",
        "",
        "_Generated from `evidence-readiness.v1.json`; do not hand-edit._",
        "",
        f"- **Project:** `{matrix['project']}`",
        f"- **Scope:** `{matrix['scope']}`",
        f"- **Baseline commit:** `{matrix['baseline_commit']}`",
        f"- **Overall status:** `{matrix['overall_status']}`",
        "",
        "## Claim ceiling",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    for field, field_value in matrix["claim_ceiling"].items():
        rendered = "true" if field_value is True else "false" if field_value is False else str(field_value)
        lines.append(f"| `{field}` | `{rendered}` |")
    lines.extend(
        [
            "",
            "## Provenance classes",
            "",
            "| Class |",
            "| --- |",
        ]
    )
    lines.extend(f"| `{provenance_class}` |" for provenance_class in matrix["provenance_classes"])
    lines.extend(
        [
            "",
            "## Lanes",
            "",
            "| ID | Title | Owner role | Status | Authorization | Evidence classes |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for lane in matrix["lanes"]:
        classes = ", ".join(f"`{item}`" for item in lane["evidence_classes"])
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{lane['id']}`",
                    _markdown_cell(lane["title"]),
                    f"`{lane['owner_role']}`",
                    f"`{lane['status']}`",
                    f"`{lane['authorization']}`",
                    classes,
                )
            )
            + " |"
        )
        lines.append(f"|  | Scope: {_markdown_cell(lane['scope'])} |  |  |  |  |")
        lines.append(
            "|  | Inputs: "
            + "<br>".join(_markdown_cell(item) for item in lane["inputs"])
            + " |  |  |  |  |"
        )
        lines.append(
            "|  | Deliverables: "
            + "<br>".join(_markdown_cell(item) for item in lane["deliverables"])
            + " |  |  |  |  |"
        )
        lines.append(
            "|  | Exit criteria: "
            + "<br>".join(
                f"{'[x]' if criterion['fulfilled'] else '[ ]'} {_markdown_cell(criterion['description'])}"
                for criterion in lane["exit_criteria"]
            )
            + " |  |  |  |  |"
        )
        lines.append(
            "|  | Non-claims: "
            + "<br>".join(_markdown_cell(item) for item in lane["non_claims"])
            + " |  |  |  |  |"
        )
    lines.extend(["", "## Blocked by", ""])
    lines.extend(f"- `{_markdown_cell(item)}`" for item in matrix["blocked_by"])
    lines.extend(["", "## Non-claims", ""])
    lines.extend(f"- {_markdown_cell(item)}" for item in matrix["non_claims"])
    lines.append("")
    return "\n".join(lines)


def check_rendered(matrix_path: str | Path, rendered_path: str | Path) -> dict[str, Any]:
    """Check a rendered document without writing either path."""

    matrix = load_matrix(matrix_path)
    expected = render_markdown(matrix).encode("utf-8")
    try:
        actual = Path(rendered_path).read_bytes()
    except OSError as error:
        raise EvidenceReadinessError("cannot read rendered readiness Markdown") from error
    if actual != expected:
        raise EvidenceReadinessError("rendered readiness Markdown is stale")
    return matrix


def _p2_string_list(value: object, label: str) -> list[str]:
    return _string_list(value, label)


def validate_p2_stop_decision(value: object) -> dict[str, Any]:
    """Validate the scoped, integrity-bound P2 stop/not_authorized record."""

    record = _closed_object(value, _P2_KEYS, "P2 stop decision")
    if record["schema"] != P2_STOP_SCHEMA or record["project"] != PROJECT:
        raise EvidenceReadinessError("P2 stop record schema or project is unsupported")
    if record["scope"] != P2_SCOPE:
        raise EvidenceReadinessError("P2 stop record is not limited to this repository/P2")
    if record["owner_role"] != "p2-project-disposition-agent":
        raise EvidenceReadinessError("P2 stop record owner role is unsupported")
    if record["decision"] != "stop" or record["status"] != "not_authorized":
        raise EvidenceReadinessError("P2 stop record decision or status is unsupported")
    if not isinstance(record["baseline_commit"], str) or not _COMMIT_RE.fullmatch(
        record["baseline_commit"]
    ):
        raise EvidenceReadinessError("P2 baseline_commit is invalid")
    basis_refs = set(_p2_string_list(record["basis_refs"], "P2 basis_refs"))
    if not _REQUIRED_P2_BASIS_REFS.issubset(basis_refs):
        raise EvidenceReadinessError("P2 stop record does not link L1-L3 state")
    _p2_string_list(record["blocked_by"], "P2 blocked_by")
    _p2_string_list(record["reopen_conditions"], "P2 reopen_conditions")
    if not _REQUIRED_P2_NON_ACTIONS.issubset(
        set(_p2_string_list(record["non_actions"], "P2 non_actions"))
    ):
        raise EvidenceReadinessError("P2 stop record omits a required non-action")
    if not _REQUIRED_P2_NON_APPLICABILITY.issubset(
        set(_p2_string_list(record["does_not_apply_to"], "P2 does_not_apply_to"))
    ):
        raise EvidenceReadinessError("P2 stop record is broader than project-local scope")
    integrity = record["record_integrity_sha256"]
    if not isinstance(integrity, str) or not re.fullmatch(r"[0-9a-f]{64}", integrity):
        raise EvidenceReadinessError("P2 record integrity hash is invalid")
    unsigned = dict(record)
    unsigned.pop("record_integrity_sha256")
    if integrity != sha256_hex(canonical_json_bytes(unsigned)):
        raise EvidenceReadinessError("P2 record integrity hash does not match")
    return record


def canonical_p2_stop_decision_bytes(value: object) -> bytes:
    record = validate_p2_stop_decision(value)
    return json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def load_p2_stop_decision(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        raw = target.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceReadinessError("cannot read a valid P2 stop decision") from error
    record = validate_p2_stop_decision(value)
    if raw != canonical_p2_stop_decision_bytes(record):
        raise EvidenceReadinessError("P2 stop decision is not canonical pretty UTF-8 JSON")
    return record
