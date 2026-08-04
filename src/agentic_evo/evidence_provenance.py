"""Closed P0/P1 provenance classes and safe project-relative namespaces.

This module deliberately does not read or interpret the Gate B laboratory
namespace.  P0/P1 writers can materialize only the two local classes with a
writer-owned namespace; no filename, lab binding, receipt digest, or test pass
can promote a record to a different class.
"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import re
from typing import Final


class EvidenceProvenanceError(ValueError):
    """Raised when a P0/P1 provenance class or namespace is unsafe."""


PROVENANCE_CLASSES: Final[tuple[str, ...]] = (
    "synthetic_fixture",
    "observed_local_test",
    "agent_authored_decision",
    "external_authorized_observation",
)

PROVENANCE_ROOTS: Final[dict[str, str | None]] = {
    "synthetic_fixture": "tests/fixtures",
    "observed_local_test": "artifacts/p0-p1/observed-local/test-results",
    "agent_authored_decision": "artifacts/p0-p1/agent-authored/model-routing",
    "external_authorized_observation": None,
}

CLAIM_CEILINGS: Final[dict[str, str]] = {
    "synthetic_fixture": "fixture/parser/verifier behavior only",
    "observed_local_test": "local test-process outcome only",
    "agent_authored_decision": "declared project decision only",
    "external_authorized_observation": (
        "only the separately verified, externally authorized contract"
    ),
}

_RESERVED_LAB_PREFIX: Final[tuple[str, ...]] = ("artifacts", "labs")
_COMPONENT_RE = re.compile(r"^[^\\/:\x00]+$")


def reject_secret_shaped(value: str, *, field: str) -> None:
    """Reject obvious credential-bearing strings before they can be persisted.

    This is intentionally a screen rather than a credential parser: P0/P1
    records have no legitimate use for credential-shaped material and therefore
    fail closed.
    """

    if not isinstance(value, str):
        raise EvidenceProvenanceError(f"{field} must be a string")
    lowered = value.casefold()
    markers = (
        "password",
        "passwd",
        "secret",
        "token",
        "credential",
        "authorization",
        "bearer ",
        "api_key",
        "api-key",
        "private_key",
        "private-key",
        "client_secret",
        "client-secret",
    )
    prefixes = ("sk-", "ghp_", "github_pat_", "akia")
    if any(marker in lowered for marker in markers) or lowered.startswith(prefixes):
        raise EvidenceProvenanceError(f"{field} is secret-shaped")


def validate_provenance_class(value: object) -> str:
    if value not in PROVENANCE_CLASSES:
        raise EvidenceProvenanceError("provenance class is not one of the four closed classes")
    return str(value)


def expected_claim_ceiling(provenance_class: str) -> str:
    validate_provenance_class(provenance_class)
    return CLAIM_CEILINGS[provenance_class]


def normalize_relative_path(value: str | Path, *, field: str = "path") -> Path:
    """Return a portable, non-empty project-relative path without dot segments."""

    try:
        raw = os.fspath(value)
    except TypeError as error:
        raise EvidenceProvenanceError(f"{field} must be a non-empty relative path") from error
    if not isinstance(raw, str) or not raw:
        raise EvidenceProvenanceError(f"{field} must be a non-empty relative path")
    if "\x00" in raw:
        raise EvidenceProvenanceError(f"{field} contains a NUL")
    windows = PureWindowsPath(raw)
    native = Path(raw)
    if (
        native.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or raw.startswith(("/", "\\"))
    ):
        raise EvidenceProvenanceError(f"{field} must be project-relative")
    components = raw.replace("\\", "/").split("/")
    if any(not component or component in {".", ".."} for component in components):
        raise EvidenceProvenanceError(f"{field} has an unsafe path component")
    if any(not _COMPONENT_RE.fullmatch(component) for component in components):
        raise EvidenceProvenanceError(f"{field} has an unsafe path component")
    return Path(*components)


def relative_text(value: str | Path, *, field: str = "path") -> str:
    return normalize_relative_path(value, field=field).as_posix()


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as error:
        raise EvidenceProvenanceError("cannot inspect path safety") from error
    return path.is_symlink() or bool(attributes & 0x400)


def _ensure_ordinary_existing_components(project_root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(project_root)
    except ValueError as error:
        raise EvidenceProvenanceError("path escapes project root") from error
    current = project_root
    if not current.is_dir() or _is_reparse_point(current):
        raise EvidenceProvenanceError("project root must be an ordinary directory")
    for index, component in enumerate(relative.parts):
        current = current / component
        if current.exists() or current.is_symlink():
            if _is_reparse_point(current):
                raise EvidenceProvenanceError("path contains a link or reparse component")
            if index < len(relative.parts) - 1 and not current.is_dir():
                raise EvidenceProvenanceError("path contains a non-directory component")


def resolve_project_path(
    project_root: str | Path,
    relative_path: str | Path,
    *,
    field: str = "path",
) -> tuple[Path, Path]:
    """Resolve a checked relative path without following project components."""

    root = Path(project_root).absolute()
    relative = normalize_relative_path(relative_path, field=field)
    target = root.joinpath(*relative.parts)
    _ensure_ordinary_existing_components(root, target)
    try:
        common = os.path.commonpath((os.fspath(root), os.fspath(target)))
    except ValueError as error:
        raise EvidenceProvenanceError("path is not contained by project root") from error
    if os.path.normcase(common) != os.path.normcase(os.fspath(root)):
        raise EvidenceProvenanceError("path escapes project root")
    return root, target


def validate_provenance_path(
    project_root: str | Path,
    provenance_class: str,
    relative_path: str | Path,
    *,
    require_exact_root: bool = False,
) -> Path:
    """Validate the one-way class-to-root mapping for a P0/P1 record path."""

    provenance_class = validate_provenance_class(provenance_class)
    root_name = PROVENANCE_ROOTS[provenance_class]
    if root_name is None:
        raise EvidenceProvenanceError(
            "external_authorized_observation has no generic P0/P1 writer"
        )
    relative = normalize_relative_path(relative_path)
    parts = relative.parts
    if tuple(part.casefold() for part in parts[:2]) == _RESERVED_LAB_PREFIX:
        raise EvidenceProvenanceError("generic P0/P1 code refuses artifacts/labs namespaces")
    expected = Path(root_name)
    if require_exact_root:
        matches = parts == expected.parts
    else:
        matches = len(parts) >= len(expected.parts) and parts[: len(expected.parts)] == expected.parts
    if not matches:
        raise EvidenceProvenanceError("provenance class and path root do not match")
    _, target = resolve_project_path(project_root, relative)
    return target


def ensure_ordinary_relative_directory(
    project_root: str | Path,
    relative_path: str | Path,
) -> Path:
    """Create a checked project-relative directory without following links.

    Existing components are rechecked one-by-one immediately before use.  This
    supports the two local P0/P1 publication roots while preserving the
    fail-closed policy for symlinks and Windows reparse points.
    """

    root, target = resolve_project_path(project_root, relative_path)
    relative = normalize_relative_path(relative_path)
    current = root
    for component in relative.parts:
        current = current / component
        if current.exists() or current.is_symlink():
            if _is_reparse_point(current) or not current.is_dir():
                raise EvidenceProvenanceError("directory is absent or unsafe")
            continue
        try:
            current.mkdir()
        except FileExistsError:
            pass
        if not current.is_dir() or _is_reparse_point(current):
            raise EvidenceProvenanceError("directory is absent or unsafe")
    if current != target:
        raise EvidenceProvenanceError("directory normalization failed")
    return target


def validate_record_provenance(
    provenance: object,
    *,
    provenance_class: str,
    producer: str,
    authorization_ref: None = None,
    claim_ceiling: str | None = None,
) -> None:
    """Validate the closed explicit provenance object used by P0/P1 records."""

    if not isinstance(provenance, dict) or set(provenance) != {
        "class",
        "producer",
        "authorization_ref",
        "claim_ceiling",
    }:
        raise EvidenceProvenanceError("provenance object is not closed")
    if provenance.get("class") != provenance_class:
        raise EvidenceProvenanceError("record provenance class is explicit and cannot be upgraded")
    if provenance.get("producer") != producer:
        raise EvidenceProvenanceError("record provenance producer does not match")
    if provenance.get("authorization_ref") is not authorization_ref:
        raise EvidenceProvenanceError("record provenance authorization reference does not match")
    expected = claim_ceiling if claim_ceiling is not None else expected_claim_ceiling(provenance_class)
    ceiling = provenance.get("claim_ceiling")
    if not isinstance(ceiling, list) or ceiling != [expected]:
        raise EvidenceProvenanceError("record provenance claim ceiling does not match its class")


def require_ordinary_file(path: Path) -> None:
    if not path.is_file() or _is_reparse_point(path):
        raise EvidenceProvenanceError("file is absent or unsafe")
