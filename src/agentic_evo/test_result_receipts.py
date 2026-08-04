"""Atomic, local-only receipts for explicitly supplied test-process commands.

This module is intentionally separate from Gate B evidence.  A receipt proves
only that this local runner observed a child process outcome; it does not
establish installation, SCM, provider, or laboratory claims.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time
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


RECEIPT_SCHEMA = "agentic-evo.test-result-receipt.v1"
RECEIPT_PRODUCER = "agentic-evo.test-result-runner.v1"
OBSERVED_LOCAL_NAMESPACE = "artifacts/p0-p1/observed-local/test-results"
DEFAULT_TIMEOUT_SECONDS = 300

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SUITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_ARGV_ITEM_BYTES = 4096
_MAX_ARGV_BYTES = 16 * 1024
_MAX_TIMEOUT_SECONDS = 24 * 60 * 60
_RECEIPT_KEYS = (
    "schema",
    "receipt_id",
    "run_id",
    "suite_id",
    "provenance",
    "project",
    "command",
    "execution",
    "output",
    "integrity_sha256",
)


class TestResultReceiptError(ValueError):
    """Raised when a local receipt command or receipt is invalid or unsafe."""


def _error_from_provenance(error: EvidenceProvenanceError) -> TestResultReceiptError:
    return TestResultReceiptError(str(error))


def _closed_object(value: object, keys: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise TestResultReceiptError(f"{label} is not a closed object")
    return value


def _require_hex(value: object, label: str, *, length: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        raise TestResultReceiptError(f"{label} must be {length} lowercase hexadecimal characters")
    return value


def _require_run_id(value: object) -> str:
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value):
        raise TestResultReceiptError("run_id must be 32 lowercase hexadecimal characters")
    return value


def _require_suite_id(value: object) -> str:
    if not isinstance(value, str) or not _SUITE_ID_RE.fullmatch(value):
        raise TestResultReceiptError("suite_id must match the local test suite identifier contract")
    return value


def _validate_argv(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise TestResultReceiptError("command argv must be a non-empty array")
    argv: list[str] = []
    total = 0
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise TestResultReceiptError("command argv items must be non-empty strings")
        byte_length = len(item.encode("utf-8"))
        if byte_length > _MAX_ARGV_ITEM_BYTES:
            raise TestResultReceiptError("command argv item exceeds the byte bound")
        total += byte_length
        if total > _MAX_ARGV_BYTES:
            raise TestResultReceiptError("command argv exceeds the byte bound")
        try:
            reject_secret_shaped(item, field=f"command argv[{index}]")
        except EvidenceProvenanceError as error:
            raise _error_from_provenance(error) from error
        argv.append(item)
    return argv


def _validate_timeout(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_TIMEOUT_SECONDS:
        raise TestResultReceiptError("timeout_seconds must be a bounded positive integer")
    return value


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_utc_iso(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise TestResultReceiptError(f"{label} must be a UTC ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TestResultReceiptError(f"{label} must be a UTC ISO-8601 string") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise TestResultReceiptError(f"{label} must be UTC")
    return parsed


def _project_state(project_root: Path) -> dict[str, object]:
    """Collect local Git state without using it as any claim beyond the receipt."""

    try:
        commit_result = subprocess.run(
            ["git", "-C", os.fspath(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        commit = commit_result.stdout.decode("ascii", "strict").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or commit_result.returncode != 0:
            commit = "unavailable"
        status_result = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(project_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            capture_output=True,
            check=False,
            timeout=5,
        )
        dirty = status_result.returncode != 0 or bool(status_result.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        commit = "unavailable"
        # Lack of an observable status must never be published as a clean tree.
        dirty = True
    return {"commit": commit, "dirty": dirty}


def _integrity(value: dict[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("integrity_sha256", None)
    return sha256_hex(canonical_json_bytes(unsigned))


def _validate_output_digest(value: object, label: str) -> dict[str, Any]:
    output = _closed_object(value, ("bytes", "sha256"), label)
    count = output["bytes"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise TestResultReceiptError(f"{label} bytes must be a non-negative integer")
    _require_hex(output["sha256"], f"{label} sha256", length=64)
    return output


def validate_test_result_receipt(value: object) -> dict[str, Any]:
    """Validate the full closed receipt shape and its self-integrity digest."""

    receipt = _closed_object(value, _RECEIPT_KEYS, "test result receipt")
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise TestResultReceiptError("receipt schema is unsupported")
    _require_hex(receipt["receipt_id"], "receipt_id", length=32)
    _require_run_id(receipt["run_id"])
    _require_suite_id(receipt["suite_id"])
    try:
        validate_record_provenance(
            receipt["provenance"],
            provenance_class="observed_local_test",
            producer=RECEIPT_PRODUCER,
        )
    except EvidenceProvenanceError as error:
        raise _error_from_provenance(error) from error

    project = _closed_object(receipt["project"], ("commit", "dirty"), "receipt project")
    if project["commit"] != "unavailable":
        _require_hex(project["commit"], "project commit", length=40)
    if not isinstance(project["dirty"], bool):
        raise TestResultReceiptError("project dirty must be a boolean")

    command = _closed_object(receipt["command"], ("argv", "argv_sha256"), "receipt command")
    argv = _validate_argv(command["argv"])
    expected_argv_sha256 = sha256_hex(canonical_json_bytes(argv))
    if command["argv_sha256"] != expected_argv_sha256:
        raise TestResultReceiptError("command argv digest does not match")

    execution = _closed_object(
        receipt["execution"],
        (
            "started_at",
            "finished_at",
            "duration_ms",
            "timeout_seconds",
            "exit_code",
            "timed_out",
            "outcome",
        ),
        "receipt execution",
    )
    started_at = _validate_utc_iso(execution["started_at"], "execution started_at")
    finished_at = _validate_utc_iso(execution["finished_at"], "execution finished_at")
    if finished_at < started_at:
        raise TestResultReceiptError("execution finished_at precedes started_at")
    duration_ms = execution["duration_ms"]
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise TestResultReceiptError("execution duration_ms must be a non-negative integer")
    _validate_timeout(execution["timeout_seconds"])
    exit_code = execution["exit_code"]
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise TestResultReceiptError("execution exit_code must be an integer or null")
    if not isinstance(execution["timed_out"], bool):
        raise TestResultReceiptError("execution timed_out must be a boolean")
    outcome = execution["outcome"]
    if outcome not in {"passed", "failed", "timed_out", "runner_error"}:
        raise TestResultReceiptError("execution outcome is unsupported")
    if outcome == "passed" and (exit_code != 0 or execution["timed_out"]):
        raise TestResultReceiptError("passed requires a zero exit code without timeout")
    if outcome == "failed" and (exit_code is None or exit_code == 0 or execution["timed_out"]):
        raise TestResultReceiptError("failed requires a non-zero exit code without timeout")
    if outcome == "timed_out" and (not execution["timed_out"] or exit_code is not None):
        raise TestResultReceiptError("timed_out outcome requires timeout with no exit code")
    if outcome == "runner_error" and (execution["timed_out"] or exit_code is not None):
        raise TestResultReceiptError("runner_error requires no timeout and no exit code")

    output = _closed_object(receipt["output"], ("stdout", "stderr"), "receipt output")
    _validate_output_digest(output["stdout"], "stdout")
    _validate_output_digest(output["stderr"], "stderr")

    integrity = receipt["integrity_sha256"]
    _require_hex(integrity, "integrity_sha256", length=64)
    if integrity != _integrity(receipt):
        raise TestResultReceiptError("receipt integrity hash does not match")
    return receipt


def _receipt_directory(
    project_root: str | Path,
    receipt_dir: str | Path,
    *,
    require_run_id: bool,
) -> tuple[Path, Path, str]:
    try:
        relative = relative_text(receipt_dir, field="receipt_dir")
        target = validate_provenance_path(
            project_root,
            "observed_local_test",
            relative,
        )
    except EvidenceProvenanceError as error:
        raise _error_from_provenance(error) from error
    parts = Path(relative).parts
    expected_parts = Path(OBSERVED_LOCAL_NAMESPACE).parts
    if len(parts) <= len(expected_parts):
        raise TestResultReceiptError("receipt_dir must name one receipt run directory")
    if len(parts) != len(expected_parts) + 1:
        raise TestResultReceiptError("receipt_dir must be directly below the receipt namespace")
    run_id = parts[-1]
    if require_run_id:
        _require_run_id(run_id)
    return Path(project_root).absolute(), target, run_id


def verify_test_result_receipt(
    project_root: str | Path,
    receipt_dir: str | Path,
) -> dict[str, Any]:
    """Verify one published local receipt without inferring a passing outcome."""

    _, directory, run_id = _receipt_directory(project_root, receipt_dir, require_run_id=True)
    try:
        if not directory.is_dir() or directory.is_symlink():
            raise TestResultReceiptError("receipt directory is absent or unsafe")
        entries = list(directory.iterdir())
    except OSError as error:
        raise TestResultReceiptError("cannot inspect receipt directory") from error
    if len(entries) != 1 or entries[0].name != "receipt.json":
        raise TestResultReceiptError("receipt directory must contain exactly receipt.json")
    receipt_path = entries[0]
    try:
        require_ordinary_file(receipt_path)
        raw = receipt_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (EvidenceProvenanceError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TestResultReceiptError("cannot read a safe receipt.json") from error
    receipt = validate_test_result_receipt(value)
    if raw != canonical_json_bytes(receipt) + b"\n":
        raise TestResultReceiptError("receipt.json is not canonical JSON with a trailing newline")
    if receipt["run_id"] != run_id:
        raise TestResultReceiptError("receipt run_id does not match its publication directory")
    return receipt


def verify_test_result_receipt_file(
    project_root: str | Path,
    receipt_file: str | Path,
) -> dict[str, Any]:
    """Verify an approved receipt path for a model-routing evidence reference."""

    try:
        relative = Path(relative_text(receipt_file, field="receipt_file"))
    except EvidenceProvenanceError as error:
        raise _error_from_provenance(error) from error
    if relative.name != "receipt.json":
        raise TestResultReceiptError("receipt_file must name receipt.json")
    return verify_test_result_receipt(project_root, relative.parent)


def _publication_namespace(project_root: str | Path, namespace: str | Path) -> tuple[Path, str]:
    try:
        relative = relative_text(namespace, field="receipt_namespace")
        validate_provenance_path(
            project_root,
            "observed_local_test",
            relative,
            require_exact_root=True,
        )
    except EvidenceProvenanceError as error:
        raise _error_from_provenance(error) from error
    if relative != OBSERVED_LOCAL_NAMESPACE:
        raise TestResultReceiptError("receipt_namespace must be the exact observed-local namespace")
    return Path(project_root).absolute(), relative


def _new_receipt(
    *,
    run_id: str,
    suite_id: str,
    argv: list[str],
    timeout_seconds: int,
    project: dict[str, object],
    started_at: datetime,
    finished_at: datetime,
    duration_ms: int,
    exit_code: int | None,
    timed_out: bool,
    outcome: str,
    stdout: bytes,
    stderr: bytes,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": uuid4().hex,
        "run_id": run_id,
        "suite_id": suite_id,
        "provenance": {
            "class": "observed_local_test",
            "producer": RECEIPT_PRODUCER,
            "authorization_ref": None,
            "claim_ceiling": ["local test-process outcome only"],
        },
        "project": project,
        "command": {
            "argv": argv,
            "argv_sha256": sha256_hex(canonical_json_bytes(argv)),
        },
        "execution": {
            "started_at": _utc_iso(started_at),
            "finished_at": _utc_iso(finished_at),
            "duration_ms": duration_ms,
            "timeout_seconds": timeout_seconds,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "outcome": outcome,
        },
        "output": {
            "stdout": {"bytes": len(stdout), "sha256": sha256_hex(stdout)},
            "stderr": {"bytes": len(stderr), "sha256": sha256_hex(stderr)},
        },
    }
    receipt["integrity_sha256"] = _integrity(receipt)
    validate_test_result_receipt(receipt)
    return receipt


def run_test_result_receipt(
    *,
    project_root: str | Path,
    receipt_namespace: str | Path,
    run_id: str,
    suite_id: str,
    timeout_seconds: int,
    argv: Sequence[str],
) -> dict[str, Any]:
    """Run argv with ``shell=False`` and publish one atomically verified receipt.

    Validation occurs before a child is spawned.  The final run directory is
    created only by a same-parent staging-directory rename and is never reused.
    """

    root, namespace_relative = _publication_namespace(project_root, receipt_namespace)
    _require_run_id(run_id)
    _require_suite_id(suite_id)
    timeout = _validate_timeout(timeout_seconds)
    checked_argv = _validate_argv(list(argv))
    if not root.is_dir() or root.is_symlink():
        raise TestResultReceiptError("project_root must be an ordinary directory")

    try:
        namespace = ensure_ordinary_relative_directory(root, namespace_relative)
    except EvidenceProvenanceError as error:
        raise _error_from_provenance(error) from error
    final_directory = namespace / run_id
    lock_path = namespace / ".receipt-publication.lock"
    with ExclusiveFileLock(lock_path):
        if final_directory.exists() or final_directory.is_symlink():
            raise TestResultReceiptError("receipt run_id directory already exists")
        staging = namespace / f".staging-{run_id}-{uuid4().hex}"
        try:
            staging.mkdir(mode=0o700)
        except OSError as error:
            raise TestResultReceiptError("cannot create receipt staging directory") from error
        started_at = datetime.now(UTC)
        started_monotonic = time.monotonic()
        stdout = b""
        stderr = b""
        exit_code: int | None = None
        timed_out = False
        outcome = "runner_error"
        try:
            completed = subprocess.run(
                checked_argv,
                cwd=root,
                shell=False,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
            outcome = "passed" if completed.returncode == 0 else "failed"
        except subprocess.TimeoutExpired as error:
            timed_out = True
            outcome = "timed_out"
            stdout = error.stdout if isinstance(error.stdout, bytes) else b""
            stderr = error.stderr if isinstance(error.stderr, bytes) else b""
        except (OSError, subprocess.SubprocessError):
            outcome = "runner_error"
        finished_at = datetime.now(UTC)
        duration_ms = max(0, round((time.monotonic() - started_monotonic) * 1000))
        receipt = _new_receipt(
            run_id=run_id,
            suite_id=suite_id,
            argv=checked_argv,
            timeout_seconds=timeout,
            project=_project_state(root),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            exit_code=exit_code,
            timed_out=timed_out,
            outcome=outcome,
            stdout=stdout,
            stderr=stderr,
        )
        try:
            atomic_write_json(staging / "receipt.json", receipt)
            raw = (staging / "receipt.json").read_bytes()
            if raw != canonical_json_bytes(receipt) + b"\n":
                raise TestResultReceiptError("staged receipt bytes are not canonical")
            validate_test_result_receipt(json.loads(raw.decode("utf-8")))
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
    return receipt


# Short aliases keep callers focused on the domain rather than the file name.
run_test_receipt = run_test_result_receipt
verify_test_receipt = verify_test_result_receipt
