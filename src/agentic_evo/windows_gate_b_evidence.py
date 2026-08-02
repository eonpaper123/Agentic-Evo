"""Read-only verification and attack exercise for the bounded Gate B receipt.

This module deliberately verifies only the narrow, externally pinned evidence
contract.  It does not install a service, request elevation, or turn a receipt
into a claim about a historical privileged execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable


ARTIFACT_NAME = "AgenticEvo.ScmProbe.exe"
MANIFEST_NAME = "gate-a-manifest.json"
_MAX_JSON_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400
_CASE_IDS = (
    "C01", "C02", "I01", "S01", "S02", "S03", "P01", "P02",
    "L01", "R01", "R02", "U01",
)
_ATTACKS = {
    "A01_artifact_tamper": "artifact_commitment_mismatch",
    "A03_coordinated_bundle_substitution": "manifest_commitment_mismatch",
    "A04_forged_passed_receipt": "receipt_claim_ceiling_exceeded",
    "A05_undeclared_bundle_entry": "undeclared_bundle_entry",
    "A07_cleanup_root_swap": "cleanup_root_swap_blocked",
}
_PLAN_KEYS = {
    "schema", "mode", "run_id", "service_name", "source_artifact",
    "artifact_sha256", "artifact_product_base", "artifact_root",
    "artifact_path", "state_product_base", "state_root", "probe_path",
    "evidence_root", "trusted_system_directory", "authorized_effects",
    "claim_ceiling",
}
_RESULT_KEYS = {
    "schema", "lab_id", "run_id", "challenge", "status", "gate_b_outcome",
    "elevated_exit_code", "elevated_pipe_client_pid", "report",
    "independent_cleanup",
}
_REPORT_KEYS = {
    "schema", "lab_id", "run_id", "challenge", "plan_sha256",
    "script_sha256", "status", "error", "elevated_administrator",
    "observation", "cleanup", "claims",
}
_EXPECTED_AUTHORIZED_EFFECTS = {
    "temporary_service": True,
    "permanent_service": False,
    "hook": False,
    "genesis": False,
    "system_restart": False,
}
_EXPECTED_CLAIM_CEILING = {
    "gate_b": "not_established",
    "restricted_service_sid_configuration": "configuration_probe_only",
    "C01": "not_run",
    "C02": "not_run",
    "I01": "not_run",
    "S01": "not_run",
    "S02": "not_run",
    "S03": "not_run",
    "P01": "not_run",
    "P02": "not_run",
    "L01": "not_run",
    "R01": "not_run",
    "R02": "not_run",
    "U01": "partial_cleanup_if_service_lifecycle_occurs",
    "native_security_verified": False,
    "ready_to_install": False,
}
_EXPECTED_REPORT_CLAIMS = {
    "gate_a_complete": False,
    "gate_b_outcome": "not_established",
    "native_security_verified": False,
    "ready_to_install": False,
    "temporary_service_created": True,
    "restricted_sid_configured": True,
    "restricted_service_sid_configuration_write": "accepted_before_cleanup",
    "reboot_validation": "not_performed_service_removed",
    "genesis_requested": False,
    "genesis_count": "not_measured",
    "all_attack_cases": "not_run",
    "U01": "partial_cleanup_pass_genesis_not_measured",
}


@dataclass(frozen=True)
class _ChildProcessResult:
    pid: int
    returncode: int
    stdout: str
    stderr: str


def reduce_case_statuses(statuses: Iterable[str]) -> str:
    """Reduce required evidence without turning an unknown into a pass."""

    values = list(statuses)
    allowed = {"not_run", "inconclusive", "failed", "passed"}
    if not values or any(value not in allowed for value in values):
        raise ValueError("case statuses must be nonempty four-state outcomes")
    if "failed" in values:
        return "failed"
    if all(value == "passed" for value in values):
        return "passed"
    if all(value == "not_run" for value in values):
        return "not_run"
    return "inconclusive"


def verify_gate_b_evidence(
    bundle_dir: Path,
    evidence_dir: Path,
    script_path: Path,
    *,
    expected_manifest_sha256: str | None,
    expected_script_sha256: str | None,
    expected_result_sha256: str | None,
    expected_lab_id: str | None,
    expected_run_id: str | None,
    expected_challenge: str | None,
) -> dict[str, Any]:
    """Recompute a bounded receipt under external commitments, without writes."""

    effects = {
        "request_elevation": False,
        "install_service": False,
        "start_service": False,
        "write_evidence": False,
        "perform_genesis": False,
    }
    failures: list[dict[str, str]] = []

    def fail(code: str, detail: str) -> None:
        failures.append({"code": code, "detail": detail})

    expected = {
        "manifest": expected_manifest_sha256,
        "script": expected_script_sha256,
        "result": expected_result_sha256,
    }
    for label, value in expected.items():
        if not _is_sha256(value):
            fail("external_anchor_missing", f"expected_{label}_sha256 is required")
    identities = {
        "lab_id": expected_lab_id,
        "run_id": expected_run_id,
        "challenge": expected_challenge,
    }
    for label, value in identities.items():
        if not isinstance(value, str) or not value:
            fail("external_identity_missing", f"expected_{label} is required")

    bundle = _absolute(bundle_dir)
    evidence = _absolute(evidence_dir)
    script = _absolute(script_path)
    manifest: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    manifest_path = bundle / MANIFEST_NAME
    artifact_path = bundle / ARTIFACT_NAME
    plan_path = evidence / "plan.json"
    result_path = evidence / "result.json"

    _require_ordinary_directory(bundle, "bundle", fail)
    _require_ordinary_directory(evidence, "evidence", fail)
    _require_ordinary_file(script, "script", fail)
    if bundle.is_dir() and not _has_reparse_component(bundle):
        entries = _ordinary_entries(bundle, "bundle", fail)
        if entries is not None and set(entries) != {ARTIFACT_NAME, MANIFEST_NAME}:
            fail("undeclared_bundle_entry", "bundle closure is not exact")
        manifest = _read_canonical_object(manifest_path, "manifest", fail)
        if manifest is not None:
            _validate_manifest(manifest, fail)
        if artifact_path.is_file() and not _is_reparse_point(artifact_path):
            _validate_artifact(artifact_path, manifest, fail)
        else:
            fail("artifact_missing", "artifact is absent or unsafe")
    if evidence.is_dir() and not _has_reparse_component(evidence):
        entries = _ordinary_entries(evidence, "evidence", fail)
        if entries is not None and set(entries) != {"plan.json", "result.json"}:
            fail("undeclared_evidence_entry", "evidence closure is not exact")
        plan = _read_canonical_object(plan_path, "plan", fail)
        result = _read_canonical_object(result_path, "result", fail)
        if result is not None:
            _validate_result_contract(result, fail)

    anchor_paths = {
        "manifest": manifest_path,
        "script": script,
        "result": result_path,
    }
    anchor_codes = {
        "manifest": "manifest_commitment_mismatch",
        "script": "script_commitment_mismatch",
        "result": "result_commitment_mismatch",
    }
    anchors: dict[str, dict[str, Any]] = {}
    for label, path in anchor_paths.items():
        actual = _sha256(path) if path.is_file() and not _is_reparse_point(path) else None
        anchored = expected[label]
        match = bool(actual and _is_sha256(anchored) and actual == anchored)
        anchors[label] = {"expected_sha256": anchored, "actual_sha256": actual, "match": match}
        if not match:
            fail(anchor_codes[label], f"external {label} commitment did not match")

    lab_id = _value(result, "lab_id")
    run_id = _value(result, "run_id")
    challenge = _value(result, "challenge")
    for label, actual in (("lab_id", lab_id), ("run_id", run_id), ("challenge", challenge)):
        expected_value = identities[label]
        if actual != expected_value:
            fail(f"{label}_mismatch", "receipt identity does not match external binding")

    if plan is not None and result is not None:
        _validate_plan_result_binding(
            plan,
            result,
            script,
            evidence,
            artifact_path,
            manifest,
            fail,
        )
        _validate_receipt_ceiling(result, fail)
    zero_residue = _observe_current_zero_residue(plan, fail) if plan is not None else False
    if not zero_residue:
        fail("current_zero_residue_not_observed", "the current planned namespace is not absent")

    case_matrix = {
        case_id: {"status": "inconclusive" if case_id == "U01" else "not_run"}
        for case_id in _CASE_IDS
    }
    passed = not failures
    claims = {
        "bounded_receipt_consistency_verified": passed,
        "gate_a_complete": False,
        "gate_b_outcome": "not_established",
        "native_security_verified": False,
        "ready_to_install": False,
        "scm_probe_bundle_ready": bool(manifest and manifest.get("claims", {}).get("scm_probe_bundle_ready") is True),
        "zero_residue_observed_now": zero_residue,
    }
    return {
        "schema": "agentic-evo.windows-gate-b-evidence-verification.v1",
        "status": "passed" if passed else "failed",
        "scope": "bounded_evidence_and_current_zero_residue",
        "lab_id": lab_id,
        "run_id": run_id,
        "challenge": challenge,
        "anchors": anchors,
        "case_matrix": case_matrix,
        "historical_configuration": {
            "status": "inconclusive",
            "reason": "receipt-only historical privileged events are not independently observed now",
        },
        "claims": claims,
        "failures": failures,
        "verifier_context": {
            "elevated": _is_elevated(),
            "effects": effects,
            "process_id": os.getpid(),
        },
    }


def exercise_gate_b_evidence_attacks(
    bundle_dir: Path,
    evidence_dir: Path,
    script_path: Path,
    **expected: str,
) -> dict[str, Any]:
    """Exercise real mutations only in disposable copies, using fresh processes."""

    source_verification = verify_gate_b_evidence(
        Path(bundle_dir),
        Path(evidence_dir),
        Path(script_path),
        expected_manifest_sha256=expected.get("expected_manifest_sha256"),
        expected_script_sha256=expected.get("expected_script_sha256"),
        expected_result_sha256=expected.get("expected_result_sha256"),
        expected_lab_id=expected.get("expected_lab_id"),
        expected_run_id=expected.get("expected_run_id"),
        expected_challenge=expected.get("expected_challenge"),
    )
    if source_verification["status"] != "passed":
        source_failures = source_verification["failures"]
        return {
            "schema": "agentic-evo.windows-gate-b-evidence-attack.v1",
            "status": "failed",
            "cases": {},
            "source_verification": source_verification,
            "source_failures": source_failures,
            "preflight_failures": source_failures,
            "effects": {
                "request_elevation": False, "install_service": False,
                "start_service": False, "perform_genesis": False,
            },
        }
    cases: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="agentic-evo-gate-b-attacks-") as temporary:
        root = Path(temporary)
        for case_id, expected_code in _ATTACKS.items():
            case_root = root / case_id
            copied_bundle = case_root / "bundle"
            copied_evidence = case_root / "evidence"
            copied_script = case_root / "windows-gate-b-experiment.ps1"
            case_root.mkdir()
            shutil.copytree(bundle_dir, copied_bundle)
            shutil.copytree(evidence_dir, copied_evidence)
            shutil.copy2(script_path, copied_script)
            cleanup_nonce = secrets.token_hex(16) if case_id == "A07_cleanup_root_swap" else None
            attacker = _run_module(
                "--attack-worker", case_id, str(copied_bundle), str(copied_evidence),
                env_overrides=(
                    {"AGENTIC_EVO_A07_CLEANUP_NONCE": cleanup_nonce}
                    if cleanup_nonce is not None else None
                ),
            )
            attacker_data = _parse_child_json(attacker, "attacker")
            if case_id == "A04_forged_passed_receipt":
                expected_for_case = dict(expected)
                expected_for_case["expected_result_sha256"] = _sha256(copied_evidence / "result.json")
            else:
                expected_for_case = dict(expected)
            if case_id == "A07_cleanup_root_swap":
                verifier = _run_module(
                    "--cleanup-attack-verifier", str(case_root), json.dumps(attacker_data),
                    env_overrides={
                        "AGENTIC_EVO_A07_EXPECTED_CLEANUP_NONCE": cleanup_nonce,
                        "AGENTIC_EVO_A07_EXPECTED_WORKER_PID": str(attacker.pid),
                    },
                )
                verifier_data = _parse_child_json(verifier, "cleanup verifier")
                observed = verifier_data.get("failure_codes", [])
                case = {
                    "role": "cleanup_defender",
                    "trust_boundary": "same_principal_harness",
                    "status": "passed" if expected_code in observed else "failed",
                    "expected_failure_code": expected_code,
                    "observed_failure_codes": observed,
                }
            else:
                descriptor = case_root / "verifier-input.json"
                descriptor.write_text(json.dumps({
                    "bundle": str(copied_bundle), "evidence": str(copied_evidence),
                    "script": str(copied_script), "expected": expected_for_case,
                }), encoding="utf-8")
                verifier = _run_module("--verify-worker", str(descriptor))
                verifier_data = _parse_child_json(verifier, "verifier")
                observed = [failure["code"] for failure in verifier_data.get("failures", [])]
                case = {
                    "role": "evidence_verifier",
                    "status": "passed" if expected_code in observed else "failed",
                    "expected_failure_code": expected_code,
                    "observed_failure_codes": observed,
                    "verifier_report": verifier_data,
                }
            case["attacker"] = {
                "pid": attacker.pid, "exit_code": attacker.returncode,
                "isolated_copy_mutation": bool(attacker_data.get("isolated_copy_mutation")),
                "action_observed": bool(attacker_data.get("action_observed")),
            }
            case["verifier"] = {"pid": verifier.pid, "exit_code": verifier.returncode}
            if attacker.returncode != 0 or verifier.returncode != 0:
                case["status"] = "failed"
            cases[case_id] = case
    return {
        "schema": "agentic-evo.windows-gate-b-evidence-attack.v1",
        "status": "passed" if all(case["status"] == "passed" for case in cases.values()) else "failed",
        "cases": cases,
        "effects": {
            "request_elevation": False, "install_service": False,
            "start_service": False, "perform_genesis": False,
        },
    }


def _validate_manifest(manifest: dict[str, Any], fail: Any) -> None:
    if manifest.get("schema") != "agentic-evo.windows-gate-a.v1":
        fail("manifest_contract_invalid", "unexpected manifest schema")
    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("file") != ARTIFACT_NAME:
        fail("manifest_contract_invalid", "artifact contract is invalid")


def _validate_artifact(path: Path, manifest: dict[str, Any] | None, fail: Any) -> None:
    if path.stat().st_size < 2 or path.read_bytes()[:2] != b"MZ":
        fail("artifact_not_portable_executable", "artifact is not a PE")
        return
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    if not isinstance(artifact, dict) or path.stat().st_size != artifact.get("bytes") or _sha256(path) != artifact.get("sha256"):
        fail("artifact_commitment_mismatch", "artifact does not match manifest")
        return
    probe = subprocess.run([str(path), "console-probe"], cwd=path.parent, capture_output=True, text=True, timeout=10, check=False)
    if probe.returncode != 1063 or probe.stdout or probe.stderr:
        fail("artifact_console_probe_invalid", "artifact did not fail closed outside SCM")


def _validate_plan_result_binding(
    plan: dict[str, Any],
    result: dict[str, Any],
    script: Path,
    evidence_dir: Path,
    bundle_artifact: Path,
    manifest: dict[str, Any] | None,
    fail: Any,
) -> None:
    if plan.get("schema") != "agentic-evo.windows-gate-b-plan.v1" or plan.get("mode") != "plan":
        fail("plan_contract_invalid", "unexpected plan schema or mode")
        return
    if set(plan) != _PLAN_KEYS:
        fail("plan_contract_invalid", "plan field set changed")
    run_id = result.get("run_id")
    if not isinstance(run_id, str) or plan.get("run_id") != run_id:
        fail("plan_run_id_mismatch", "plan and result run IDs differ")
    expected_paths = _expected_plan_paths(
        run_id,
        evidence_dir=evidence_dir,
        bundle_artifact=bundle_artifact,
        manifest=manifest,
    )
    if plan.get("service_name") != expected_paths["service_name"]:
        fail("plan_target_derivation_invalid", "service name is not derived from run ID")
    if not _same_path_text(plan.get("source_artifact"), expected_paths["source_artifact"]):
        fail("plan_target_derivation_invalid", "source artifact is not verifier-derived")
    if plan.get("artifact_sha256") != expected_paths["artifact_sha256"]:
        fail("plan_target_derivation_invalid", "artifact sha256 is not verifier-derived")
    if not _same_path_text(plan.get("artifact_product_base"), expected_paths["artifact_product_base"]):
        fail("plan_target_derivation_invalid", "artifact product base is not verifier-derived")
    if not _same_path_text(plan.get("artifact_root"), expected_paths["artifact_root"]):
        fail("plan_target_derivation_invalid", "artifact root is not verifier-derived")
    if not _same_path_text(plan.get("artifact_path"), expected_paths["artifact_path"]):
        fail("plan_target_derivation_invalid", "artifact path is not verifier-derived")
    if not _same_path_text(plan.get("state_product_base"), expected_paths["state_product_base"]):
        fail("plan_target_derivation_invalid", "state product base is not verifier-derived")
    if not _same_path_text(plan.get("state_root"), expected_paths["state_root"]):
        fail("plan_target_derivation_invalid", "state root is not verifier-derived")
    if not _same_path_text(plan.get("probe_path"), expected_paths["probe_path"]):
        fail("plan_target_derivation_invalid", "probe path is not verifier-derived")
    if not _same_path_text(plan.get("evidence_root"), expected_paths["evidence_root"]):
        fail("plan_target_derivation_invalid", "evidence root is not verifier-derived")
    if not _same_path_text(plan.get("trusted_system_directory"), expected_paths["trusted_system_directory"]):
        fail("trusted_scm_query_unavailable", "trusted system directory is not verifier-derived")
    if plan.get("authorized_effects") != _EXPECTED_AUTHORIZED_EFFECTS:
        fail("plan_contract_invalid", "authorized effects changed")
    if plan.get("claim_ceiling") != _EXPECTED_CLAIM_CEILING:
        fail("plan_contract_invalid", "claim ceiling changed")
    raw_plan = (Path(plan.get("evidence_root", "")) / "plan.json")
    # The evidence path in a plan is descriptive; digest the verified sibling instead.
    del raw_plan
    plan_digest = _sha256_without_final_newline_from_object(plan)
    report = result.get("report")
    if not isinstance(report, dict):
        fail("result_report_missing", "result has no report object")
        return
    if report.get("schema") != "agentic-evo.windows-gate-b-config-probe.v1":
        fail("report_contract_invalid", "unexpected report schema")
    if report.get("plan_sha256") != plan_digest:
        fail("report_plan_binding_mismatch", "report plan digest did not match")
    if report.get("script_sha256") != _sha256(script):
        fail("report_script_binding_mismatch", "report script digest did not match")
    for field in ("lab_id", "run_id", "challenge"):
        if report.get(field) != result.get(field):
            fail(f"report_{field}_mismatch", "report identity did not match result")
    observation = report.get("observation")
    if not isinstance(observation, dict) or observation.get("artifact_sha256") != plan.get("artifact_sha256"):
        fail("report_observation_binding_mismatch", "observation does not bind planned artifact")
    cleanup = report.get("cleanup")
    independent = result.get("independent_cleanup")
    if not isinstance(cleanup, dict) or not isinstance(independent, dict):
        fail("cleanup_receipt_missing", "cleanup receipts are missing")


def _validate_receipt_ceiling(result: dict[str, Any], fail: Any) -> None:
    report = result.get("report")
    claims = report.get("claims") if isinstance(report, dict) else None
    forbidden = (
        result.get("gate_b_outcome") != "not_established"
        or result.get("schema") != "agentic-evo.windows-gate-b-result.v1"
        or not isinstance(claims, dict)
        or claims.get("gate_a_complete") is not False
        or claims.get("gate_b_outcome") != "not_established"
        or claims.get("native_security_verified") is not False
        or claims.get("ready_to_install") is not False
        or set(claims) != set(_EXPECTED_REPORT_CLAIMS)
        or any(claims.get(key) != value for key, value in _EXPECTED_REPORT_CLAIMS.items())
    )
    if forbidden:
        fail("receipt_claim_ceiling_exceeded", "receipt asserted a claim above the bounded ceiling")


def _observe_current_zero_residue(plan: dict[str, Any], fail: Any) -> bool:
    artifact_root = plan.get("artifact_root")
    state_root = plan.get("state_root")
    service_name = plan.get("service_name")
    if not all(isinstance(value, str) and value for value in (artifact_root, state_root, service_name)):
        fail("plan_target_derivation_invalid", "plan lacks current-residue targets")
        return False
    paths_absent = not Path(artifact_root).exists() and not Path(state_root).exists()
    sc = _trusted_sc_path()
    if not sc.is_file() or _is_reparse_point(sc):
        fail("trusted_scm_query_unavailable", "trusted sc.exe is unavailable")
        return False
    check = subprocess.run([str(sc), "query", service_name], capture_output=True, text=True, timeout=10, check=False)
    # 1060 is the documented not-found code.  Any successful query means residue.
    service_absent = check.returncode == 1060
    return paths_absent and service_absent


def _read_canonical_object(path: Path, label: str, fail: Any) -> dict[str, Any] | None:
    if not path.is_file() or _is_reparse_point(path):
        fail(f"{label}_missing", f"{label} is absent or unsafe")
        return None
    if path.stat().st_size > _MAX_JSON_BYTES:
        fail(f"{label}_too_large", f"{label} exceeds bounded receipt size")
        return None
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail(f"{label}_not_canonical_json", f"{label} is not UTF-8 JSON")
        return None
    if not isinstance(value, dict) or raw != _canonical_json(value):
        fail(f"{label}_not_canonical_json", f"{label} is not compact canonical JSON")
        return None
    return value


def _ordinary_entries(path: Path, label: str, fail: Any) -> dict[str, Path] | None:
    try:
        entries = {entry.name: entry for entry in path.iterdir()}
    except OSError:
        fail(f"{label}_unreadable", f"cannot read {label}")
        return None
    if any(entry.is_symlink() or _is_reparse_point(entry) for entry in entries.values()):
        fail(f"{label}_reparse_entry", f"{label} contains a reparse entry")
    return entries


def _require_ordinary_directory(path: Path, label: str, fail: Any) -> None:
    if not path.is_dir() or path.is_symlink() or _has_reparse_component(path):
        fail(f"{label}_unsafe_path", f"{label} must be an ordinary directory")


def _require_ordinary_file(path: Path, label: str, fail: Any) -> None:
    if not path.is_file() or path.is_symlink() or _has_reparse_component(path):
        fail(f"{label}_unsafe_path", f"{label} must be an ordinary file")


def _run_module(
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> _ChildProcessResult:
    environment = dict(os.environ)
    if env_overrides:
        environment.update(env_overrides)
    source_root = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    child = subprocess.Popen(
        [sys.executable, "-m", "agentic_evo.windows_gate_b_evidence", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    stdout, stderr = child.communicate(timeout=45)
    return _ChildProcessResult(child.pid, child.returncode, stdout, stderr)


def _parse_child_json(process: _ChildProcessResult, label: str) -> dict[str, Any]:
    if process.returncode != 0:
        return {"error": f"{label} failed: {process.stderr}", "isolated_copy_mutation": False, "action_observed": False}
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {"error": f"{label} emitted invalid JSON", "isolated_copy_mutation": False, "action_observed": False}
    return value if isinstance(value, dict) else {"error": f"{label} did not emit object"}


def _attack_worker(
    case_id: str,
    bundle: Path,
    evidence: Path,
    *,
    cleanup_nonce: str | None = None,
) -> dict[str, Any]:
    if case_id == "A01_artifact_tamper":
        artifact = bundle / ARTIFACT_NAME
        artifact.write_bytes(artifact.read_bytes() + b"attacker-tail")
    elif case_id == "A03_coordinated_bundle_substitution":
        artifact = bundle / ARTIFACT_NAME
        artifact.write_bytes(artifact.read_bytes() + b"coordinated-substitution")
        manifest_path = bundle / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact"]["bytes"] = artifact.stat().st_size
        manifest["artifact"]["sha256"] = _sha256(artifact)
        manifest_path.write_bytes(_canonical_json(manifest, sort_keys=True))
    elif case_id == "A04_forged_passed_receipt":
        result_path = evidence / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["gate_b_outcome"] = "passed"
        result["report"]["claims"].update({"gate_a_complete": True, "native_security_verified": True, "ready_to_install": True})
        result_path.write_bytes(_canonical_json(result))
    elif case_id == "A05_undeclared_bundle_entry":
        (bundle / "undeclared-attacker-entry.txt").write_text("attack", encoding="utf-8")
    elif case_id == "A07_cleanup_root_swap":
        from agentic_evo import windows_gate_a as gate_a
        target = _absolute(bundle)
        backup = _absolute(bundle.parent / "bundle-before-swap")
        external = _absolute(bundle.parent / "external-target")
        external.mkdir()
        shutil.copy2(bundle / ARTIFACT_NAME, external / ARTIFACT_NAME)
        shutil.copy2(bundle / MANIFEST_NAME, external / MANIFEST_NAME)
        sentinel = external / "sentinel.txt"
        sentinel.write_text("must survive", encoding="utf-8")
        original_verify = gate_a._verify_bundle_contents
        outcome = {"blocked": False, "swapped": False}
        def swap(path: Path, *, expected: dict[str, Any] | None = None) -> dict[str, Any]:
            verified = original_verify(path, expected=expected)
            try:
                path.rename(backup)
            except OSError:
                outcome["blocked"] = True
                return verified
            link = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(path), str(external)], capture_output=True, text=True, timeout=10, check=False)
            if link.returncode == 0:
                outcome["swapped"] = True
            return verified
        from unittest import mock
        with mock.patch.object(gate_a, "_verify_bundle_contents", side_effect=swap):
            gate_a.cleanup_gate_a_bundle(bundle)
        attempt = {
            "schema": "agentic-evo.windows-gate-b-cleanup-attempt.v2",
            "case_id": "A07_cleanup_root_swap",
            "cleanup_nonce": cleanup_nonce,
            "worker_pid": os.getpid(),
            "phase": "after_bundle_verification_before_rename",
            "target": str(target),
            "backup": str(backup),
            "external_target": str(external),
            "external_sha256": {
                name: _sha256(external / name)
                for name in (ARTIFACT_NAME, MANIFEST_NAME, "sentinel.txt")
            },
            "blocked": outcome["blocked"],
            "swapped": outcome["swapped"],
        }
        return {
            "worker_pid": os.getpid(),
            "cleanup_nonce": cleanup_nonce,
            "cleanup_attempt": attempt,
            "isolated_copy_mutation": True,
            "action_observed": outcome["blocked"] and not outcome["swapped"],
        }
    else:
        raise ValueError("unknown attack")
    return {"isolated_copy_mutation": True, "action_observed": True}


def _cleanup_attack_verifier(
    case_root: Path,
    attack: dict[str, Any],
    *,
    expected_cleanup_nonce: str,
    expected_worker_pid: int | str,
) -> dict[str, Any]:
    if not attack:
        return {"failure_codes": ["cleanup_attempt_not_observed"]}
    if not isinstance(attack, dict):
        return {"failure_codes": ["cleanup_attempt_binding_invalid"]}

    expected_bundle = _absolute(case_root / "bundle")
    expected_backup = _absolute(case_root / "bundle-before-swap")
    expected_external = _absolute(case_root / "external-target")
    external_files = {ARTIFACT_NAME, MANIFEST_NAME, "sentinel.txt"}
    report_keys = {
        "worker_pid", "cleanup_nonce", "cleanup_attempt",
        "isolated_copy_mutation", "action_observed",
    }
    attempt_keys = {
        "schema", "case_id", "cleanup_nonce", "worker_pid", "phase",
        "target", "backup", "external_target", "external_sha256", "blocked",
        "swapped",
    }
    attempt = attack.get("cleanup_attempt")
    try:
        trusted_worker_pid = int(expected_worker_pid)
    except (TypeError, ValueError):
        trusted_worker_pid = -1
    bindings_valid = (
        set(attack) == report_keys
        and isinstance(attempt, dict)
        and set(attempt) == attempt_keys
        and isinstance(expected_cleanup_nonce, str)
        and trusted_worker_pid > 0
        and type(attack.get("worker_pid")) is int
        and attack.get("worker_pid") == trusted_worker_pid
        and attack.get("cleanup_nonce") == expected_cleanup_nonce
        and attempt.get("schema") == "agentic-evo.windows-gate-b-cleanup-attempt.v2"
        and attempt.get("case_id") == "A07_cleanup_root_swap"
        and attempt.get("cleanup_nonce") == expected_cleanup_nonce
        and attempt.get("worker_pid") == trusted_worker_pid
        and attempt.get("worker_pid") == attack.get("worker_pid")
        and attempt.get("phase") == "after_bundle_verification_before_rename"
        and isinstance(attempt.get("target"), str)
        and isinstance(attempt.get("backup"), str)
        and isinstance(attempt.get("external_target"), str)
        and Path(attempt["target"]).is_absolute()
        and Path(attempt["backup"]).is_absolute()
        and Path(attempt["external_target"]).is_absolute()
        and _same_path_text(attempt["target"], str(expected_bundle))
        and _same_path_text(attempt["backup"], str(expected_backup))
        and _same_path_text(attempt["external_target"], str(expected_external))
        and isinstance(attempt.get("external_sha256"), dict)
        and set(attempt["external_sha256"]) == external_files
        and all(_is_sha256(value) for value in attempt["external_sha256"].values())
    )
    if not bindings_valid:
        return {"failure_codes": ["cleanup_attempt_binding_invalid"]}
    if (
        attack.get("action_observed") is not True
        or attempt.get("blocked") is not True
        or attempt.get("swapped") is not False
    ):
        return {"failure_codes": ["cleanup_attempt_not_observed"]}

    external = Path(attempt["external_target"])
    bundle = Path(attempt["target"])
    backup = Path(attempt["backup"])
    external_files = (ARTIFACT_NAME, MANIFEST_NAME, "sentinel.txt")
    exact_external = (
        external.is_dir()
        and not external.is_symlink()
        and not _has_reparse_component(external)
        and (entries := _ordinary_entries(external, "external", lambda _code, _detail: None)) is not None
        and set(entries) == set(external_files)
        and all(
            entry.is_file() and not entry.is_symlink() and not _is_reparse_point(entry)
            for entry in entries.values()
        )
    )
    intact = (
        exact_external
        and (external / "sentinel.txt").read_text(encoding="utf-8") == "must survive"
    )
    checksums = attempt["external_sha256"]
    intact = intact and all(
        _sha256(external / name) == checksums[name]
        for name in external_files
    )
    blocked = not backup.exists() and not backup.is_symlink() and not _is_reparse_point(backup)
    original_removed = not bundle.exists() and not bundle.is_symlink() and not _is_reparse_point(bundle)
    return {
        "failure_codes": [
            "cleanup_root_swap_blocked"
        ] if intact and blocked and original_removed else ["cleanup_poststate_inconsistent"]
    }


def _validate_result_contract(result: dict[str, Any], fail: Any) -> None:
    if set(result) != _RESULT_KEYS:
        fail("result_contract_invalid", "result field set changed")
    if result.get("schema") != "agentic-evo.windows-gate-b-result.v1":
        fail("result_contract_invalid", "unexpected result schema")
    if result.get("status") != "configuration_probe_completed":
        fail("result_contract_invalid", "result status is not a completed configuration probe")
    if result.get("gate_b_outcome") != "not_established":
        fail("result_contract_invalid", "result gate B outcome exceeds the bounded contract")
    if type(result.get("elevated_exit_code")) is not int or result.get("elevated_exit_code") != 0:
        fail("result_contract_invalid", "result elevated exit code is not the expected success code")
    if (
        type(result.get("elevated_pipe_client_pid")) is not int
        or result.get("elevated_pipe_client_pid") <= 0
    ):
        fail("result_contract_invalid", "result elevated pipe client PID is invalid")
    report = result.get("report")
    if not isinstance(report, dict):
        fail("result_report_missing", "result has no report object")
        return
    if set(report) != _REPORT_KEYS:
        fail("report_contract_invalid", "report field set changed")
    if report.get("schema") != "agentic-evo.windows-gate-b-config-probe.v1":
        fail("report_contract_invalid", "unexpected report schema")
    if report.get("status") != "configuration_probe_completed":
        fail("report_contract_invalid", "report status is not a completed configuration probe")
    if report.get("error") != "":
        fail("report_contract_invalid", "report recorded an error")
    if report.get("elevated_administrator") is not True:
        fail("report_contract_invalid", "report does not attest an elevated administrator")


def _expected_plan_paths(
    run_id: str,
    *,
    evidence_dir: Path,
    bundle_artifact: Path,
    manifest: dict[str, Any] | None,
) -> dict[str, str]:
    artifact_product_base = str(Path(_program_files_dir()) / "Agentic-Evo")
    state_product_base = str(Path(_program_data_dir()) / "Agentic-Evo")
    artifact_root = str(Path(artifact_product_base) / "GateB" / run_id)
    state_root = str(Path(state_product_base) / "GateB" / run_id)
    artifact_contract = manifest.get("artifact") if isinstance(manifest, dict) else None
    artifact_sha256 = artifact_contract.get("sha256") if isinstance(artifact_contract, dict) else None
    return {
        "service_name": f"AgenticEvoGateB_{run_id}",
        "source_artifact": str(bundle_artifact),
        "artifact_sha256": artifact_sha256,
        "artifact_product_base": artifact_product_base,
        "artifact_root": artifact_root,
        "artifact_path": str(Path(artifact_root) / ARTIFACT_NAME),
        "state_product_base": state_product_base,
        "state_root": state_root,
        "probe_path": str(Path(state_root) / "scm-write.probe"),
        "evidence_root": str(evidence_dir),
        "trusted_system_directory": str(_trusted_system_directory()),
    }


def _same_path_text(actual: Any, expected: str) -> bool:
    return isinstance(actual, str) and os.path.normcase(os.path.normpath(actual)) == os.path.normcase(os.path.normpath(expected))


def _program_files_dir() -> str:
    return os.environ.get("ProgramFiles", r"C:\Program Files")


def _program_data_dir() -> str:
    return os.environ.get("ProgramData", r"C:\ProgramData")


def _trusted_system_directory() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


def _trusted_sc_path() -> Path:
    return _trusted_system_directory() / "sc.exe"


def _canonical_json(value: dict[str, Any], *, sort_keys: bool = False) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys) + "\n").encode("utf-8")


def _sha256_without_final_newline_from_object(value: dict[str, Any]) -> str:
    # Plan receipts are canonical JSON.  The script hashes its compact text before
    # Write-AtomicJson appends the final newline.
    return hashlib.sha256(_canonical_json(value)[:-1]).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _value(value: dict[str, Any] | None, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_reparse_point(path: Path) -> bool:
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return False


def _has_reparse_component(path: Path) -> bool:
    current = path
    while True:
        if _is_reparse_point(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(2)
    mode = sys.argv[1]
    arguments = sys.argv[2:]
    if mode not in {"--attack-worker", "--verify-worker", "--cleanup-attack-verifier"}:
        raise SystemExit(2)
    if mode == "--attack-worker":
        if len(arguments) != 3:
            raise SystemExit(2)
        result = _attack_worker(
            arguments[0],
            Path(arguments[1]),
            Path(arguments[2]),
            cleanup_nonce=os.environ.get("AGENTIC_EVO_A07_CLEANUP_NONCE"),
        )
    elif mode == "--verify-worker":
        if len(arguments) != 1:
            raise SystemExit(2)
        descriptor = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
        result = verify_gate_b_evidence(Path(descriptor["bundle"]), Path(descriptor["evidence"]), Path(descriptor["script"]), **descriptor["expected"])
    else:
        if len(arguments) != 2:
            raise SystemExit(2)
        result = _cleanup_attack_verifier(
            Path(arguments[0]),
            json.loads(arguments[1]),
            expected_cleanup_nonce=os.environ.get(
                "AGENTIC_EVO_A07_EXPECTED_CLEANUP_NONCE", "",
            ),
            expected_worker_pid=os.environ.get(
                "AGENTIC_EVO_A07_EXPECTED_WORKER_PID", "",
            ),
        )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
