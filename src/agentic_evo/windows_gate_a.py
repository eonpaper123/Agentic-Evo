from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from typing import Any
from uuid import uuid4


ARTIFACT_NAME = "AgenticEvo.ScmProbe.exe"
MANIFEST_NAME = "gate-a-manifest.json"
_MAX_MANIFEST_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400


class GateABundleError(RuntimeError):
    """A Gate A bundle could not be prepared or verified safely."""


def prepare_gate_a_bundle(output_dir: Path) -> dict[str, Any]:
    """Build one exact, no-UAC Windows SCM probe bundle."""

    _require_windows()
    output = _absolute_path(output_dir)
    parent = output.parent
    if output.exists() or output.is_symlink() or _is_reparse_point(output):
        raise GateABundleError("output directory already exists")
    if _is_reparse_point(parent) or not parent.is_dir():
        raise GateABundleError("output parent must be an existing ordinary directory")

    staging = parent / f".{output.name}.gate-a-{uuid4().hex}.tmp"
    staging.mkdir()
    try:
        source_bytes = _source_path().read_bytes()
        compiler = _find_system_compiler()
        compiler_version = _compiler_version(compiler)
        source_copy = staging / "AgenticEvo.ScmProbe.cs"
        source_copy.write_bytes(source_bytes)
        artifact = staging / ARTIFACT_NAME
        _compile_probe(
            compiler,
            source_copy=source_copy,
            artifact=artifact,
            staging=staging,
        )
        source_copy.unlink()
        _assert_portable_executable(artifact)

        manifest = _build_manifest(
            source_sha256=_sha256_bytes(source_bytes),
            compiler_sha256=_sha256_file(compiler),
            compiler_version=compiler_version,
            artifact_sha256=_sha256_file(artifact),
            artifact_size=artifact.stat().st_size,
        )
        _write_canonical_json(staging / MANIFEST_NAME, manifest)
        _verify_bundle_contents(staging, expected=manifest)
        os.replace(staging, output)
        return verify_gate_a_bundle(output)
    except BaseException:
        _remove_staging_directory(staging)
        raise


def verify_gate_a_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Independently re-hash and exercise one uninstalled Gate A bundle."""

    _require_windows()
    bundle = _absolute_path(bundle_dir)
    return _verify_bundle_contents(bundle)


def cleanup_gate_a_bundle(bundle_dir: Path) -> dict[str, str]:
    """Remove only a verified local Gate A bundle; never touch SCM or state."""

    _require_windows()
    bundle = _absolute_path(bundle_dir)
    if not bundle.exists():
        return {"status": "already_absent"}
    _verify_bundle_contents(bundle)
    for name in (ARTIFACT_NAME, MANIFEST_NAME):
        (bundle / name).unlink()
    bundle.rmdir()
    return {"status": "local_artifacts_removed"}


def _verify_bundle_contents(
    bundle: Path,
    *,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _is_reparse_point(bundle) or not bundle.is_dir():
        raise GateABundleError("bundle must be an ordinary directory")
    entries = {entry.name: entry for entry in bundle.iterdir()}
    if set(entries) != {ARTIFACT_NAME, MANIFEST_NAME}:
        raise GateABundleError("bundle contains undeclared files")
    if any(entry.is_symlink() or _is_reparse_point(entry) for entry in entries.values()):
        raise GateABundleError("bundle cannot contain links or reparse points")

    manifest_path = entries[MANIFEST_NAME]
    if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise GateABundleError("manifest is too large")
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
        raise GateABundleError("manifest is not canonical UTF-8 JSON") from error
    if not isinstance(manifest, dict):
        raise GateABundleError("manifest root must be an object")
    if raw_manifest != _canonical_json_bytes(manifest):
        raise GateABundleError("manifest is not in canonical form")
    if expected is not None and manifest != expected:
        raise GateABundleError("manifest changed while preparing the bundle")
    _validate_manifest_contract(manifest)
    if manifest["build"]["source"]["sha256"] != _sha256_file(_source_path()):
        raise GateABundleError("packaged source no longer matches the manifest")
    compiler = _find_system_compiler()
    if manifest["build"]["compiler"]["sha256"] != _sha256_file(compiler):
        raise GateABundleError("system compiler no longer matches the manifest")
    if manifest["build"]["compiler"]["version"] != _compiler_version(compiler):
        raise GateABundleError("system compiler version no longer matches the manifest")

    artifact = entries[ARTIFACT_NAME]
    artifact_contract = manifest["artifact"]
    if artifact.stat().st_size != artifact_contract["bytes"]:
        raise GateABundleError("artifact size does not match the manifest")
    if _sha256_file(artifact) != artifact_contract["sha256"]:
        raise GateABundleError("artifact digest does not match the manifest")
    _assert_portable_executable(artifact)
    _run_console_probe(artifact)
    return manifest


def _validate_manifest_contract(manifest: dict[str, Any]) -> None:
    try:
        if manifest["schema"] != "agentic-evo.windows-gate-a.v1":
            raise GateABundleError("unexpected manifest schema")
        if manifest["gate"] != "A" or manifest["status"] != "partial":
            raise GateABundleError("unexpected Gate A probe status")
        if manifest["artifact"]["file"] != ARTIFACT_NAME:
            raise GateABundleError("unexpected artifact path")
        if manifest["claims"]["scm_probe_bundle_ready"] is not True:
            raise GateABundleError("SCM probe bundle readiness is not asserted")
        if manifest["claims"]["gate_a_complete"] is not False:
            raise GateABundleError("Gate A completion is overclaimed")
        for false_claim in (
            "privileged_installation_executed",
            "scm_observed",
            "service_token_observed",
            "state_acl_attacked",
            "native_security_verified",
            "ready_to_install",
        ):
            if manifest["claims"][false_claim] is not False:
                raise GateABundleError(f"forbidden claim is true: {false_claim}")
        if any(manifest["effects"].values()):
            raise GateABundleError("Gate A manifest contains an installation effect")
        case_ids = {case["id"] for case in manifest["gate_b_case_matrix"]}
        if case_ids != {
            "C01",
            "C02",
            "I01",
            "S01",
            "S02",
            "S03",
            "P01",
            "P02",
            "L01",
            "R01",
            "R02",
            "U01",
        }:
            raise GateABundleError("Gate B case matrix is incomplete")
        if manifest["missing_gate_a_components"] != [
            "executable_gate_b_cleanup",
            "independent_verifier",
            "real_attacker",
            "trusted_elevated_handoff",
        ]:
            raise GateABundleError("Gate A missing-component boundary changed")
        _validate_relative_name(manifest["artifact"]["file"])
    except (KeyError, TypeError, ValueError) as error:
        raise GateABundleError("manifest contract is malformed") from error


def _build_manifest(
    *,
    source_sha256: str,
    compiler_sha256: str,
    compiler_version: str,
    artifact_sha256: str,
    artifact_size: int,
) -> dict[str, Any]:
    cases = [
        _case("C01", "independent_verifier", "query stable SCM PID and token"),
        _case("C02", "unprivileged_host", "request service mutation rights"),
        _case("I01", "unprivileged_host", "attack exact artifact operations"),
        _case("S01", "unprivileged_host", "attack protected state operations"),
        _case("S02", "scm_witness", "commit and checkpoint trusted state"),
        _case("S03", "restricted_body", "attack protected state operations"),
        _case("P01", "bound_host_client", "exercise public allowlist"),
        _case("P02", "untrusted_clients", "attack public peer boundary"),
        _case("L01", "stale_lineage_actor", "replay lineage capability"),
        _case("R01", "experiment_runner", "crash and restart service"),
        _case("R02", "administrator", "request SCM supervisor stop"),
        _case("U01", "experiment_runner", "remove all temporary objects"),
    ]
    return {
        "artifact": {
            "bytes": artifact_size,
            "console_probe": {
                "command": [ARTIFACT_NAME, "console-probe"],
                "expected_exit_code": 1063,
                "meaning": "ERROR_FAILED_SERVICE_CONTROLLER_CONNECT",
            },
            "file": ARTIFACT_NAME,
            "runtime_closure": [
                "protected_probe_artifact",
                "windows_system_dotnet_framework",
            ],
            "sha256": artifact_sha256,
        },
        "build": {
            "compiler": {
                "family": "windows_system_dotnet_framework_csc",
                "sha256": compiler_sha256,
                "version": compiler_version,
            },
            "determinism": (
                "exact_source_compiler_and_per_build_artifact_hashes;"
                "cross_build_pe_byte_identity_not_claimed"
            ),
            "source": {
                "file": "agentic_evo/native/AgenticEvo.ScmProbe.cs",
                "sha256": source_sha256,
            },
        },
        "claims": {
            "gate_a_complete": False,
            "native_security_verified": False,
            "privileged_installation_executed": False,
            "ready_to_install": False,
            "scm_probe_bundle_ready": True,
            "scm_observed": False,
            "service_token_observed": False,
            "state_acl_attacked": False,
        },
        "cleanup": {
            "genesis_home_forbidden": True,
            "manifest_targets_only": True,
            "ordered_operations": [
                "stop_service",
                "delete_service",
                "wait_until_service_absent",
                "delete_state_root",
                "delete_artifact_root",
                "assert_no_residue",
            ],
            "random_namespace_required": True,
        },
        "effects": {
            "create_protected_state": False,
            "install_hook": False,
            "install_service": False,
            "perform_genesis": False,
            "request_elevation": False,
            "start_service": False,
        },
        "gate": "A",
        "gate_b_case_matrix": cases,
        "missing_gate_a_components": [
            "executable_gate_b_cleanup",
            "independent_verifier",
            "real_attacker",
            "trusted_elevated_handoff",
        ],
        "protected_target_contract": {
            "account": "NT AUTHORITY\\LocalService",
            "artifact_dacl": {
                "allowed": [
                    "SYSTEM:full_control",
                    "Administrators:full_control",
                    "service_sid:read_execute",
                ],
                "inheritance": "blocked",
                "protected": True,
            },
            "body_service_sid_access": "forbidden",
            "image_path": (
                "\"<protected_artifact_root>\\AgenticEvo.ScmProbe.exe\" service "
                "--service-name <random_service_name> "
                "--probe-path "
                "\"<protected_state_root>\\scm-write.probe\""
            ),
            "service_name": {
                "pattern": "AgenticEvoGateB_<32_lower_hex>",
                "random_namespace_required": True,
            },
            "service_sid_type": "RESTRICTED",
            "service_type": "SERVICE_WIN32_OWN_PROCESS",
            "state_closure": [
                "directory",
                "database",
                "key_material",
                "sqlite_journal",
                "sqlite_shm",
                "sqlite_temp",
                "sqlite_wal",
            ],
            "state_dacl": {
                "allowed": [
                    "SYSTEM:full_control",
                    "service_sid:full_control",
                ],
                "inheritance": "blocked",
                "protected": True,
            },
            "start_type": "demand",
        },
        "schema": "agentic-evo.windows-gate-a.v1",
        "status": "partial",
    }


def _case(case_id: str, actor: str, action: str) -> dict[str, str]:
    return {
        "action": action,
        "actor": actor,
        "gate_a_status": "not_run",
        "id": case_id,
    }


def _compile_probe(
    compiler: Path,
    *,
    source_copy: Path,
    artifact: Path,
    staging: Path,
) -> None:
    windows_directory = _windows_directory()
    environment = {
        "SystemRoot": str(windows_directory),
        "WINDIR": str(windows_directory),
        "TEMP": str(staging),
        "TMP": str(staging),
    }
    result = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            "/optimize+",
            "/platform:anycpu",
            f"/out:{artifact}",
            str(source_copy),
        ],
        cwd=staging,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not artifact.is_file():
        raise GateABundleError(
            f"Windows system compiler failed with exit code {result.returncode}"
        )


def _run_console_probe(artifact: Path) -> None:
    result = subprocess.run(
        [str(artifact), "console-probe"],
        cwd=artifact.parent,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 1063 or result.stdout or result.stderr:
        raise GateABundleError("SCM entrypoint did not fail closed outside SCM")


def _find_system_compiler() -> Path:
    windows_directory = _windows_directory()
    candidates = (
        windows_directory
        / "Microsoft.NET"
        / "Framework64"
        / "v4.0.30319"
        / "csc.exe",
        windows_directory
        / "Microsoft.NET"
        / "Framework"
        / "v4.0.30319"
        / "csc.exe",
    )
    for candidate in candidates:
        if candidate.is_file() and not _is_reparse_point(candidate):
            return candidate
    raise GateABundleError("Windows system C# compiler was not found")


def _compiler_version(compiler: Path) -> str:
    result = subprocess.run(
        [str(compiler), "/help"],
        cwd=compiler.parent,
        env={
            "SystemRoot": str(_windows_directory()),
            "WINDIR": str(_windows_directory()),
        },
        capture_output=True,
        timeout=10,
        check=False,
    )
    stdout = result.stdout.decode("ascii", errors="replace")
    first_line = next(
        (line.strip() for line in stdout.splitlines() if line.strip()),
        "",
    )
    if result.returncode != 0 or not first_line:
        raise GateABundleError("could not identify the Windows system compiler")
    return first_line


def _windows_directory() -> Path:
    import ctypes

    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise GateABundleError("could not resolve the Windows directory")
    return Path(buffer.value).resolve()


def _source_path() -> Path:
    source = Path(__file__).resolve().parent / "native" / "AgenticEvo.ScmProbe.cs"
    if not source.is_file():
        raise GateABundleError("packaged SCM probe source is missing")
    return source


def _assert_portable_executable(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise GateABundleError("compiled probe is not a Windows PE executable")
    except OSError as error:
        raise GateABundleError("compiled probe could not be read") from error


def _write_canonical_json(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(_canonical_json_bytes(value))


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_relative_name(value: str) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or value in {"", ".", ".."}
        or "\\" in value
    ):
        raise GateABundleError("manifest contains an unsafe relative path")


def _absolute_path(value: Path) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT)


def _remove_staging_directory(staging: Path) -> None:
    if (
        staging.exists()
        and staging.parent.is_dir()
        and staging.name.startswith(".")
        and ".gate-a-" in staging.name
        and staging.name.endswith(".tmp")
    ):
        shutil.rmtree(staging)


def _require_windows() -> None:
    if sys.platform != "win32":
        raise GateABundleError("unsupported_host_platform")
