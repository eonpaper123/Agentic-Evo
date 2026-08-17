from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from .adapters.codex import handle_codex_hook
from .adapters.opencode import handle_opencode_hook
from .autonomous_loop import smoke_main as autonomous_loop_smoke_main
from ._util import canonical_json_bytes
from .errors import (
    AgenticEvoError,
    GenesisExistsError,
    MemoryIntegrityError,
    MemoryRecordError,
)
from .loop_integration import DefectWorkspace, run_loop_demo
from .experiment_pack import (
    EXPERIMENT_CLAIM_CEILING,
    _ExperimentArtifactConsistencyError,
    export_experiment_pack,
    export_experiment_prereg,
    verify_experiment_artifact,
)
from .install_plan import build_install_plan
from .ipc import (
    InvalidPublicFrame,
    OffRehearsalClient,
    ServiceRejectedError,
    ServiceUnavailableError,
    SurfaceClient,
    new_public_request_id,
    validate_public_request_frame,
)
from .memory_store import MemoryStore
from .observer import observe, report_json, report_markdown
from .service import main as service_main
from .runtime import DevelopmentalRuntime
from .runtime_adopt import RuntimeAdoptError, adopt_genesis_home
from .trusted import TRUSTED_SCHEMA_VERSION, TrustedState
from .windows_gate_a import (
    GateABundleError,
    cleanup_gate_a_bundle,
    prepare_gate_a_bundle,
    verify_gate_a_bundle,
)


MAX_HOOK_INPUT_BYTES = 2 * 1024 * 1024
GENESIS_INSTRUMENT_VERSION = "agentic-evo-cli-genesis-v1"
SURFACE_STDIO_SCHEMA = "agentic-evo.surface-stdio.v1"
MAX_SURFACE_STDIO_TEXT_BYTES = 1024
MAX_SURFACE_STDIO_PAYLOAD_BYTES = 60 * 1024


class _SurfaceStdioInputError(ValueError):
    pass


def _write_json(value: Mapping[str, Any], *, stream: Any = None) -> None:
    target = sys.stdout if stream is None else stream
    print(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=target,
        flush=True,
    )


def _observer_paths_alias(first: Path, second: Path) -> bool:
    first_absolute = os.path.normcase(os.path.abspath(os.fspath(first)))
    second_absolute = os.path.normcase(os.path.abspath(os.fspath(second)))
    if first_absolute == second_absolute:
        return True

    first_resolved = os.path.normcase(os.fspath(Path(first_absolute).resolve(strict=False)))
    second_resolved = os.path.normcase(os.fspath(Path(second_absolute).resolve(strict=False)))
    if first_resolved == second_resolved:
        return True

    if os.path.exists(first_absolute) and os.path.exists(second_absolute):
        return os.path.samefile(first_absolute, second_absolute)
    return False


def _preflight_observer_outputs(
    source: Path,
    json_output: Path | None,
    markdown_output: Path | None,
) -> None:
    outputs = [path for path in (json_output, markdown_output) if path is not None]
    pairs = [(source, output) for output in outputs]
    if len(outputs) == 2:
        pairs.append((outputs[0], outputs[1]))
    if any(_observer_paths_alias(first, second) for first, second in pairs):
        raise ValueError("observer input and output paths must be distinct")


def _read_hook_input() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def _surface_status(home: Path) -> int:
    try:
        result = SurfaceClient(home).status()
    except AgenticEvoError:
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": "service_unavailable",
                    "message": "Witness service is unavailable",
                },
            },
            stream=sys.stderr,
        )
        return 4
    _write_json({"ok": True, "result": result})
    return 0


def _off_rehearsal(home: Path) -> int:
    try:
        result = OffRehearsalClient(home).off()
    except AgenticEvoError:
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": "control_unavailable",
                    "message": "Witness Off rehearsal is unavailable",
                },
            },
            stream=sys.stderr,
        )
        return 4
    _write_json({"ok": True, "result": result})
    return 0


def _surface_hook(home: Path, surface: str) -> int:
    payload = _read_hook_input()
    if payload is None:
        return 0
    if surface == "opencode":
        result = handle_opencode_hook(home, payload)
    else:
        result = handle_codex_hook(home, payload)
    if result is not None:
        _write_json(result)
    return 0


def _surface_stdio_error(
    request_id: str | None,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "schema": SURFACE_STDIO_SCHEMA,
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _surface_stdio_text(
    value: Any,
    field: str,
    *,
    optional: bool = False,
) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        message = (
            f"{field} must be a string or null"
            if optional
            else f"{field} must be a non-empty string"
        )
        raise _SurfaceStdioInputError(message)
    if not optional and not value:
        raise _SurfaceStdioInputError(f"{field} must be a non-empty string")
    if len(value.encode("utf-8")) > MAX_SURFACE_STDIO_TEXT_BYTES:
        raise _SurfaceStdioInputError(
            f"{field} exceeds the stdio text byte bound"
        )
    return value


def _surface_stdio_request(
    raw_line: str,
    client: SurfaceClient,
    execution_surface: str,
) -> dict[str, Any]:
    try:
        request = json.loads(raw_line)
    except (json.JSONDecodeError, RecursionError):
        return _surface_stdio_error(
            None,
            "invalid_input",
            "line must be one UTF-8 JSON object",
        )
    if not isinstance(request, dict):
        return _surface_stdio_error(
            None,
            "invalid_input",
            "line must be one UTF-8 JSON object",
        )

    request_id = request.get("id") if isinstance(request.get("id"), str) else None
    if set(request) != {"schema", "id", "op", "args"}:
        return _surface_stdio_error(
            request_id,
            "invalid_input",
            "request envelope has unexpected fields",
        )
    if request["schema"] != SURFACE_STDIO_SCHEMA:
        return _surface_stdio_error(
            request_id,
            "invalid_input",
            "request schema is unsupported",
        )
    try:
        request_id = _surface_stdio_text(request["id"], "id")
    except ValueError:
        return _surface_stdio_error(
            request_id,
            "invalid_input",
            "id must be a bounded non-empty string",
        )
    if request["op"] not in ("status", "wake", "observe", "sleep"):
        return _surface_stdio_error(request_id, "invalid_input", "op is unsupported")
    if not isinstance(request["args"], dict):
        return _surface_stdio_error(
            request_id,
            "invalid_input",
            "args must be one JSON object",
        )

    operation = request["op"]
    args = request["args"]
    required = {
        "status": set(),
        "wake": {"session_id", "project_environment"},
        "observe": {"event_kind", "payload"},
        "sleep": {"session_id"},
    }[operation]
    optional = {
        "status": set(),
        "wake": {"model"},
        "observe": {
            "session_id",
            "turn_id",
            "tool_call_id",
            "project_environment",
            "occurred_at",
            "correlation_ref",
            "causation_ref",
            "parent_ref",
            "coverage_gap",
        },
        "sleep": set(),
    }[operation]
    if set(args) != required | optional.intersection(args):
        return _surface_stdio_error(
            request_id,
            "invalid_input",
            "operation arguments do not match the stdio contract",
        )

    try:
        if operation == "wake":
            params = {
                "execution_surface": execution_surface,
                "session_id": _surface_stdio_text(args.get("session_id"), "session_id"),
                "project_environment": _surface_stdio_text(
                    args.get("project_environment"), "project_environment"
                ),
                "model": _surface_stdio_text(args.get("model"), "model", optional=True),
            }
        elif operation == "observe":
            event_kind = _surface_stdio_text(args.get("event_kind"), "event_kind")
            payload = args.get("payload")
            if not isinstance(payload, dict):
                raise _SurfaceStdioInputError(
                    "operation arguments do not match the stdio contract"
                )
            if len(canonical_json_bytes(payload)) > MAX_SURFACE_STDIO_PAYLOAD_BYTES:
                raise _SurfaceStdioInputError(
                    "payload exceeds the stdio payload byte bound"
                )
            occurred_at = _surface_stdio_text(
                args.get("occurred_at"), "occurred_at", optional=True
            )
            correlation_ref = _surface_stdio_text(
                args.get("correlation_ref"), "correlation_ref", optional=True
            )
            causation_ref = _surface_stdio_text(
                args.get("causation_ref"), "causation_ref", optional=True
            )
            parent_ref = _surface_stdio_text(
                args.get("parent_ref"), "parent_ref", optional=True
            )
            params = {
                "event_kind": event_kind,
                "payload": payload,
                "execution_surface": execution_surface,
                "session_id": _surface_stdio_text(
                    args.get("session_id"), "session_id", optional=True
                ),
                "turn_id": _surface_stdio_text(
                    args.get("turn_id"), "turn_id", optional=True
                ),
                "tool_call_id": _surface_stdio_text(
                    args.get("tool_call_id"), "tool_call_id", optional=True
                ),
                "project_environment": _surface_stdio_text(
                    args.get("project_environment"),
                    "project_environment",
                    optional=True,
                ),
                "coverage_gap": _surface_stdio_text(
                    args.get("coverage_gap"), "coverage_gap", optional=True
                ),
            }
            if occurred_at is not None:
                params["occurred_at"] = occurred_at
            if correlation_ref is not None:
                params["correlation_ref"] = correlation_ref
            if causation_ref is not None:
                params["causation_ref"] = causation_ref
            if parent_ref is not None:
                params["parent_ref"] = parent_ref
        elif operation == "sleep":
            params = {
                "execution_surface": execution_surface,
                "session_id": _surface_stdio_text(args.get("session_id"), "session_id"),
            }
        else:
            params = {}
    except _SurfaceStdioInputError as error:
        return _surface_stdio_error(request_id, "invalid_input", str(error))

    if operation != "status":
        try:
            validate_public_request_frame(
                operation,
                params,
                request_id=new_public_request_id(),
            )
        except InvalidPublicFrame:
            return _surface_stdio_error(
                request_id,
                "invalid_input",
                "operation arguments exceed the public frame byte bound",
            )

    try:
        if operation == "status":
            result = client.status()
        elif operation == "wake":
            result = client.wake(**params)
        elif operation == "observe":
            result = client.observe(**params)
        else:
            result = client.sleep(**params)
    except ServiceRejectedError as error:
        return _surface_stdio_error(request_id, error.code, str(error))
    except ServiceUnavailableError:
        return _surface_stdio_error(
            request_id,
            "service_unavailable",
            "Witness service is unavailable",
        )
    except ValueError:
        return _surface_stdio_error(
            request_id,
            "surface_error",
            "Surface request failed",
        )
    except (AgenticEvoError, OSError, TimeoutError, TypeError):
        return _surface_stdio_error(
            request_id,
            "surface_error",
            "Surface request failed",
        )
    return {
        "schema": SURFACE_STDIO_SCHEMA,
        "id": request_id,
        "ok": True,
        "result": result,
    }


def _surface_stdio(home: Path, execution_surface: str) -> int:
    client = SurfaceClient(home)
    for raw_line in sys.stdin:
        if raw_line.strip():
            _write_json(_surface_stdio_request(raw_line, client, execution_surface))
    return 0


def _surface_stdio_execution_surface(value: str) -> str:
    try:
        return _surface_stdio_text(value, "execution_surface") or ""
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _gate_b_evidence_arguments(command: argparse.ArgumentParser) -> None:
    """Add the complete, externally pinned Gate B evidence contract."""

    command.add_argument(
        "--bundle-dir",
        type=Path,
        required=True,
        help="Existing local Gate A bundle to verify; never installs a service.",
    )
    command.add_argument(
        "--evidence-dir",
        type=Path,
        required=True,
        help="Existing Gate B evidence directory; verification never writes it.",
    )
    command.add_argument(
        "--gate-b-script",
        "--script",
        dest="gate_b_script",
        type=Path,
        required=True,
        help="Exact Windows Gate B experiment script to verify by digest.",
    )
    command.add_argument(
        "--expected-manifest-sha256",
        required=True,
        help="Externally pinned SHA-256 for gate-a-manifest.json.",
    )
    command.add_argument(
        "--expected-script-sha256",
        required=True,
        help="Externally pinned SHA-256 for the Gate B script.",
    )
    command.add_argument(
        "--expected-result-sha256",
        required=True,
        help="Externally pinned SHA-256 for evidence/result.json.",
    )
    command.add_argument(
        "--lab-id",
        required=True,
        help="Non-secret declared laboratory identifier.",
    )
    command.add_argument(
        "--expected-run-id",
        required=True,
        help="Expected experiment run identifier.",
    )
    command.add_argument(
        "--expected-challenge",
        required=True,
        help="Expected experiment challenge identifier.",
    )


def _gate_b_evidence(arguments: argparse.Namespace, *, attack: bool) -> int:
    """Run the read-only verifier or its isolated-copy attacker harness."""

    from .windows_gate_b_evidence import (
        exercise_gate_b_evidence_attacks,
        verify_gate_b_evidence,
    )

    operation = (
        exercise_gate_b_evidence_attacks if attack else verify_gate_b_evidence
    )
    try:
        result = operation(
            arguments.bundle_dir,
            arguments.evidence_dir,
            arguments.gate_b_script,
            expected_manifest_sha256=arguments.expected_manifest_sha256,
            expected_script_sha256=arguments.expected_script_sha256,
            expected_result_sha256=arguments.expected_result_sha256,
            expected_lab_id=arguments.lab_id,
            expected_run_id=arguments.expected_run_id,
            expected_challenge=arguments.expected_challenge,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": "windows_gate_b_evidence_error",
                    "message": str(error),
                },
            },
            stream=sys.stderr,
        )
        return 6
    if result.get("status") != "passed":
        _write_json({"ok": False, "result": result}, stream=sys.stderr)
        return 7
    _write_json({"ok": True, "result": result})
    return 0


def _loop_demo_run(arguments: argparse.Namespace) -> int:
    """Run the real-task autonomous-loop demo against fresh caller-owned dirs."""

    try:
        workspace = DefectWorkspace.create(
            arguments.workspace_dir,
            defect=arguments.defect,
        )
        result = run_loop_demo(workspace, arguments.loop_home)
    except (AgenticEvoError, OSError, ValueError, subprocess.SubprocessError) as error:
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": "loop_demo_run_error",
                    "message": str(error),
                },
            },
            stream=sys.stderr,
        )
        return 6
    _write_json({"ok": True, "result": result})
    return 0 if result.get("final_module_passed") else 7


def _experiment_artifact_error(error: Exception) -> int:
    _write_json(
        {
            "ok": False,
            "error": {
                "code": "experiment_artifact_error",
                "message": str(error),
            },
        },
        stream=sys.stderr,
    )
    return 6


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact must be a JSON object")
    for candidate in (value, value.get("prereg")):
        if isinstance(candidate, dict) and isinstance(
            ceiling := candidate.get("claim_ceiling"), dict
        ):
            candidate["claim_ceiling"] = {
                key: ceiling[key]
                for key in EXPERIMENT_CLAIM_CEILING
                if key in ceiling
            } | {
                key: item
                for key, item in ceiling.items()
                if key not in EXPERIMENT_CLAIM_CEILING
            }
    return value


def _export_experiment_prereg(arguments: argparse.Namespace) -> int:
    try:
        result = export_experiment_prereg(
            DevelopmentalRuntime.load(arguments.dev_home),
            hypothesis_refs=arguments.hypothesis_ref or [],
            control_refs=arguments.control_ref or [],
        )
    except Exception as error:
        return _experiment_artifact_error(error)
    _write_json({"ok": True, "result": result})
    return 0


def _export_experiment_pack(arguments: argparse.Namespace) -> int:
    try:
        prereg = _json_object(arguments.prereg)
        result = export_experiment_pack(
            DevelopmentalRuntime.load(arguments.dev_home),
            prereg,
            end_sequence=arguments.end_sequence,
        )
    except Exception as error:
        return _experiment_artifact_error(error)
    _write_json({"ok": True, "result": result})
    return 0


def _verify_experiment_artifact(arguments: argparse.Namespace) -> int:
    try:
        result = verify_experiment_artifact(_json_object(arguments.artifact))
    except _ExperimentArtifactConsistencyError as error:
        _write_json(
            {"ok": False, "result": {"code": error.code, "message": str(error)}},
            stream=sys.stderr,
        )
        return 7
    except Exception as error:
        return _experiment_artifact_error(error)
    _write_json({"ok": True, "result": result})
    return 0


MEMORY_STORE_DEFAULT = Path("memory/camus.jsonl")


def _memory_store_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--store",
        type=Path,
        default=MEMORY_STORE_DEFAULT,
        help=(
            "CAMU store JSONL path; default memory/camus.jsonl under the "
            "current directory."
        ),
    )


def _open_memory_store(path: Path, *, create: bool = False) -> MemoryStore:
    if create and not path.exists():
        return MemoryStore.create(path)
    return MemoryStore.load(path)


def _memory_error(error: Exception) -> int:
    _write_json(
        {
            "ok": False,
            "error": {"code": "memory_error", "message": str(error)},
        },
        stream=sys.stderr,
    )
    return 6


def _memory_integrity_error(error: Exception) -> int:
    _write_json(
        {
            "ok": False,
            "result": {"verified": False, "message": str(error)},
        },
        stream=sys.stderr,
    )
    return 7


def _memory_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError("must be true or false")


def _memory_camu_add(arguments: argparse.Namespace) -> int:
    if (arguments.record is None) == (arguments.record_file is None):
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": "memory_error",
                    "message": "exactly one of --record or --record-file is required",
                },
            },
            stream=sys.stderr,
        )
        return 6
    if arguments.record is not None:
        try:
            record = json.loads(arguments.record)
        except (json.JSONDecodeError, RecursionError) as exc:
            return _memory_error(
                ValueError(f"--record must be one JSON object: {exc}")
            )
    else:
        try:
            record = json.loads(arguments.record_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, RecursionError) as exc:
            return _memory_error(exc)
    if not isinstance(record, dict):
        return _memory_error(
            MemoryRecordError("CAMU record must be one JSON object")
        )
    try:
        store = _open_memory_store(arguments.store, create=True)
        camu_id = store.add_camu(record)
    except (MemoryIntegrityError, MemoryRecordError, OSError, ValueError) as exc:
        return _memory_error(exc)
    _write_json({"ok": True, "result": {"id": camu_id}})
    return 0


def _memory_camu_list(arguments: argparse.Namespace) -> int:
    try:
        store = _open_memory_store(arguments.store)
        camus = store.list()
    except (MemoryIntegrityError, MemoryRecordError, OSError, ValueError) as exc:
        return _memory_error(exc)
    summary = [
        {
            "id": item["id"],
            "influence_domain": item["record"]["I"]["domain"],
            "prediction_status": item["record"]["P"]["status"],
            "condition": item["record"]["P"]["condition"],
            "recorded_at": item["recorded_at"],
        }
        for item in camus
    ]
    _write_json({"ok": True, "result": {"count": len(summary), "camus": summary}})
    return 0


def _memory_camu_show(arguments: argparse.Namespace) -> int:
    try:
        store = _open_memory_store(arguments.store)
        result = store.get(arguments.id)
    except (MemoryIntegrityError, MemoryRecordError, OSError, ValueError) as exc:
        return _memory_error(exc)
    _write_json({"ok": True, "result": result})
    return 0


def _memory_camu_outcome(arguments: argparse.Namespace) -> int:
    observed: Any = None
    if arguments.observed is not None:
        try:
            observed = json.loads(arguments.observed)
        except (json.JSONDecodeError, RecursionError) as exc:
            return _memory_error(ValueError(f"--observed must be JSON: {exc}"))
    try:
        store = _open_memory_store(arguments.store)
        result = store.record_outcome(
            arguments.id, observed=observed, matched=arguments.matched
        )
    except (MemoryIntegrityError, MemoryRecordError, OSError, ValueError) as exc:
        return _memory_error(exc)
    _write_json(
        {
            "ok": True,
            "result": {
                "id": result["id"],
                "prediction_status": result["record"]["P"]["status"],
            },
        }
    )
    return 0


def _memory_recall(arguments: argparse.Namespace) -> int:
    try:
        context = json.loads(arguments.context)
    except (json.JSONDecodeError, RecursionError) as exc:
        return _memory_error(ValueError(f"--context must be JSON: {exc}"))
    try:
        store = _open_memory_store(arguments.store)
        matches = store.recall(context)
    except (MemoryIntegrityError, MemoryRecordError, OSError, ValueError) as exc:
        return _memory_error(exc)
    _write_json({"ok": True, "result": {"matches": matches}})
    return 0


def _memory_consolidate(arguments: argparse.Namespace) -> int:
    try:
        store = _open_memory_store(arguments.store)
        summary = store.consolidate(ttl_seconds=arguments.ttl_days * 24 * 60 * 60)
    except (MemoryIntegrityError, MemoryRecordError, OSError, ValueError) as exc:
        return _memory_error(exc)
    _write_json({"ok": True, "result": summary})
    return 0


def _memory_verify_chain(arguments: argparse.Namespace) -> int:
    try:
        store = _open_memory_store(arguments.store)
        store.verify_chain()
    except MemoryIntegrityError as exc:
        return _memory_integrity_error(exc)
    except (OSError, ValueError) as exc:
        return _memory_error(exc)
    _write_json({"ok": True, "result": {"verified": True, "records": store.count()}})
    return 0


def _genesis_arguments(command: argparse.ArgumentParser) -> None:
    """Add the complete, externally confirmed Genesis parameter contract."""

    command.add_argument(
        "--home",
        type=Path,
        required=True,
        help=(
            "New permanent home for one sovereign identity; must not already "
            "contain Genesis or be a non-empty state directory."
        ),
    )
    command.add_argument(
        "--host-binding",
        required=True,
        help="Host identity string bound at birth; stored only as who=sha256(host-binding).",
    )
    command.add_argument(
        "--purpose-anchor",
        required=True,
        help="Non-secret purpose anchor string; stored only as why=sha256(purpose-anchor).",
    )
    command.add_argument(
        "--root",
        required=True,
        help="Exact Root commitment for the new identity lineage.",
    )
    command.add_argument(
        "--initial-head",
        required=True,
        help="Exact initial Head commitment for the new identity lineage.",
    )


def _genesis(arguments: argparse.Namespace) -> int:
    """Birth the permanent sovereign identity of one new home (irreversible)."""

    home = Path(arguments.home).resolve()
    try:
        if TrustedState.has_genesis(home):
            raise GenesisExistsError("home already contains a Genesis")
        if TrustedState.has_genesis(home / "trusted"):
            raise GenesisExistsError("home looks like an existing runtime home")
        if home.exists() and any(home.iterdir()):
            raise GenesisExistsError("home is not an empty state directory")
        trusted = TrustedState.genesis(
            home,
            host_binding=arguments.host_binding,
            purpose_anchor=arguments.purpose_anchor,
            root=arguments.root,
            initial_head=arguments.initial_head,
            instrument_version=GENESIS_INSTRUMENT_VERSION,
            protocol_version=TRUSTED_SCHEMA_VERSION,
            genesis_payload={
                "trusted_schema": TRUSTED_SCHEMA_VERSION,
                "source": "genesis_cli",
            },
        )
    except (AgenticEvoError, OSError) as error:
        _write_json(
            {
                "ok": False,
                "error": {"code": "genesis_error", "message": str(error)},
            },
            stream=sys.stderr,
        )
        return 6
    snapshot = trusted.snapshot()
    genesis_record = trusted.records()[0]
    _write_json(
        {
            "ok": True,
            "result": {
                "who": snapshot.who,
                "why": snapshot.why,
                "root": snapshot.root,
                "head": snapshot.head,
                "authority": snapshot.authority,
                "evidence_ref": {
                    "sequence": genesis_record.sequence,
                    "event_id": genesis_record.event_id,
                    "integrity_hash": genesis_record.integrity_hash,
                },
                "home": str(home),
            },
        }
    )
    return 0


def _runtime_adopt_arguments(command: argparse.ArgumentParser) -> None:
    """Add the runtime-adopt parameter contract."""

    command.add_argument(
        "--home",
        type=Path,
        required=True,
        help=(
            "Genesis-born home (state.sqlite3 + witness.key at the home root) "
            "to restructure into the servable runtime layout; never runs on a "
            "dev-home."
        ),
    )


def _runtime_adopt(arguments: argparse.Namespace) -> int:
    """Adopt a Genesis-born home into the servable runtime layout."""

    home = Path(arguments.home).resolve()
    try:
        result = adopt_genesis_home(home)
    except (AgenticEvoError, OSError, ValueError) as error:
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": "runtime_adopt_error",
                    "message": str(error),
                },
            },
            stream=sys.stderr,
        )
        return 6
    _write_json({"ok": True, "result": result})
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-evo",
        description="Agentic-Evo Pre-Genesis development surface.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("serve", "Run the fixed-home foreground Witness rehearsal."),
        ("status", "Read status through the public Surface."),
        ("off", "Request Off through the unauthenticated control rehearsal."),
        ("hook", "Handle one bounded Codex or opencode lifecycle hook from stdin."),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--dev-home",
            type=Path,
            required=True,
            help="Existing disposable runtime home; never performs Genesis.",
        )
        if name == "hook":
            command.add_argument(
                "--surface",
                choices=("codex", "opencode"),
                default="codex",
                help="Coding-agent hook adapter to dispatch to (default: codex).",
            )
    genesis = commands.add_parser(
        "genesis",
        help=(
            "Birth the permanent sovereign identity of one new home "
            "(irreversible; never runs on a dev-home)."
        ),
    )
    _genesis_arguments(genesis)
    runtime_adopt = commands.add_parser(
        "runtime-adopt",
        help=(
            "Adopt a Genesis-born home into the servable runtime layout "
            "(identity preserved; never runs on a dev-home)."
        ),
    )
    _runtime_adopt_arguments(runtime_adopt)
    surface_stdio = commands.add_parser(
        "surface-stdio",
        help="Bridge a bounded generic stdio protocol to the public Surface.",
    )
    surface_stdio.add_argument(
        "--dev-home",
        type=Path,
        required=True,
        help="Existing disposable runtime home; never performs Genesis.",
    )
    surface_stdio.add_argument(
        "--execution-surface",
        type=_surface_stdio_execution_surface,
        required=True,
    )
    commands.add_parser(
        "plan-install",
        help="Print a deterministic plan that performs no installation writes.",
    )
    observer = commands.add_parser(
        "observe",
        help="Read Coding Agent receipts and emit a deterministic safe report.",
    )
    observer.add_argument("source", type=Path)
    observer.add_argument(
        "--agent-kind", choices=("auto", "codex", "lingtai"), default="auto"
    )
    observer.add_argument("--format", choices=("json", "markdown"), default="json")
    observer.add_argument("--json-output", type=Path)
    observer.add_argument("--markdown-output", type=Path)
    loop_smoke = commands.add_parser(
        "autonomous-loop-smoke",
        help="Run an in-memory autonomous-loop smoke fixture and write JSONL records.",
    )
    loop_smoke.add_argument("--output", type=Path, required=True)
    loop_smoke.add_argument("--fixture", choices=("passed", "failed"), default="passed")
    loop_smoke.add_argument("--promotion-passes", type=int, default=2)
    loop_demo = commands.add_parser(
        "loop-demo-run",
        help=(
            "Run the real-task autonomous-loop demo "
            "(synthetic defect, real file/test/patch flow)."
        ),
    )
    loop_demo.add_argument(
        "--workspace-dir",
        type=Path,
        required=True,
        help="Fresh caller-owned directory for the demo module + unittest.",
    )
    loop_demo.add_argument(
        "--loop-home",
        type=Path,
        required=True,
        help="Fresh autonomous-loop home (meta/events/consolidation stores).",
    )
    loop_demo.add_argument("--defect", default="off_by_one")
    prereg = commands.add_parser(
        "export-experiment-prereg",
        help="Export a detached experiment preregistration artifact.",
    )
    prereg.add_argument("--dev-home", type=Path, required=True)
    prereg.add_argument("--hypothesis-ref", action="append")
    prereg.add_argument("--control-ref", action="append")
    pack = commands.add_parser(
        "export-experiment-pack",
        help="Export a detached experiment evidence pack.",
    )
    pack.add_argument("--dev-home", type=Path, required=True)
    pack.add_argument("--prereg", type=Path, required=True)
    pack.add_argument("--end-sequence", type=int, required=True)
    artifact = commands.add_parser(
        "verify-experiment-artifact",
        help="Verify a detached experiment artifact without loading a runtime.",
    )
    artifact.add_argument("--artifact", type=Path, required=True)
    for name, help_text in (
        (
            "prepare-windows-gate-a",
            "Build an exact no-UAC Windows SCM experiment bundle.",
        ),
        (
            "verify-windows-gate-a",
            "Re-hash and exercise an uninstalled Windows Gate A bundle.",
        ),
        (
            "cleanup-windows-gate-a",
            "Remove only a verified local Windows Gate A bundle.",
        ),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--bundle-dir",
            type=Path,
            required=True,
            help="Explicit local Gate A bundle directory.",
        )
    for name, help_text in (
        (
            "verify-windows-gate-b-evidence",
            "Verify externally pinned Gate B evidence without privileged effects.",
        ),
        (
            "attack-windows-gate-b-evidence",
            "Exercise Gate B evidence attacks in isolated temporary copies.",
        ),
    ):
        command = commands.add_parser(name, help=help_text)
        _gate_b_evidence_arguments(command)
    memory_list = commands.add_parser(
        "memory-camu-list",
        help="List effective CAMU records in a Body-owned memory store.",
    )
    _memory_store_arguments(memory_list)
    memory_add = commands.add_parser(
        "memory-camu-add",
        help="Add one content-addressed CAMU record to a Body-owned memory store.",
    )
    _memory_store_arguments(memory_add)
    memory_add.add_argument(
        "--record",
        help="Inline JSON CAMU record (one object).",
    )
    memory_add.add_argument(
        "--record-file",
        type=Path,
        help="Path to a file containing one JSON CAMU record object.",
    )
    memory_show = commands.add_parser(
        "memory-camu-show",
        help="Show one effective CAMU record by id.",
    )
    _memory_store_arguments(memory_show)
    memory_show.add_argument(
        "--id",
        required=True,
        help="CAMU content-address id.",
    )
    memory_outcome = commands.add_parser(
        "memory-camu-outcome",
        help="Record one outcome and apply pending/verified/contradicted bookkeeping.",
    )
    _memory_store_arguments(memory_outcome)
    memory_outcome.add_argument(
        "--id",
        required=True,
        help="CAMU content-address id.",
    )
    memory_outcome.add_argument(
        "--observed",
        help="Optional JSON value observed after the prediction window.",
    )
    memory_outcome.add_argument(
        "--matched",
        type=_memory_bool,
        required=True,
        help="Whether the observed result supported the prediction (true/false).",
    )
    memory_recall = commands.add_parser(
        "memory-recall",
        help="Recall matching CAMU ids with the default placeholder evaluator.",
    )
    _memory_store_arguments(memory_recall)
    memory_recall.add_argument(
        "--context",
        required=True,
        help="JSON context object evaluated against each CAMU activation.",
    )
    memory_consolidate = commands.add_parser(
        "memory-consolidate",
        help="Sleep-consolidation scaffold: mark stale pending predictions overdue.",
    )
    _memory_store_arguments(memory_consolidate)
    memory_consolidate.add_argument(
        "--ttl-days",
        type=int,
        default=7,
        help="Age in days after which a pending prediction becomes overdue.",
    )
    memory_verify = commands.add_parser(
        "memory-verify-chain",
        help="Verify the memory store hash chain.",
    )
    _memory_store_arguments(memory_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "genesis":
        return _genesis(arguments)
    if arguments.command == "runtime-adopt":
        return _runtime_adopt(arguments)
    if arguments.command == "serve":
        return service_main(["--dev-home", str(arguments.dev_home)])
    if arguments.command == "status":
        return _surface_status(arguments.dev_home)
    if arguments.command == "off":
        return _off_rehearsal(arguments.dev_home)
    if arguments.command == "hook":
        return _surface_hook(arguments.dev_home, arguments.surface)
    if arguments.command == "surface-stdio":
        return _surface_stdio(arguments.dev_home, arguments.execution_surface)
    if arguments.command == "plan-install":
        _write_json(build_install_plan())
        return 0
    if arguments.command == "observe":
        try:
            _preflight_observer_outputs(
                arguments.source,
                arguments.json_output,
                arguments.markdown_output,
            )
            report = observe(arguments.source, agent_kind=arguments.agent_kind)
            json_text = report_json(report)
            markdown_text = report_markdown(report)
            if arguments.json_output is not None:
                arguments.json_output.write_text(json_text, encoding="utf-8")
            if arguments.markdown_output is not None:
                arguments.markdown_output.write_text(markdown_text, encoding="utf-8")
            sys.stdout.write(json_text if arguments.format == "json" else markdown_text)
            return 0
        except (OSError, UnicodeError, ValueError):
            _write_json(
                {
                    "ok": False,
                    "error": {
                        "code": "observer_input_error",
                        "message": "Observer source or output could not be processed",
                    },
                },
                stream=sys.stderr,
            )
            return 6
    if arguments.command == "export-experiment-prereg":
        return _export_experiment_prereg(arguments)
    if arguments.command == "export-experiment-pack":
        return _export_experiment_pack(arguments)
    if arguments.command == "verify-experiment-artifact":
        return _verify_experiment_artifact(arguments)
    if arguments.command in {
        "prepare-windows-gate-a",
        "verify-windows-gate-a",
        "cleanup-windows-gate-a",
    }:
        try:
            if arguments.command == "prepare-windows-gate-a":
                result = prepare_gate_a_bundle(arguments.bundle_dir)
            elif arguments.command == "verify-windows-gate-a":
                result = verify_gate_a_bundle(arguments.bundle_dir)
            else:
                result = cleanup_gate_a_bundle(arguments.bundle_dir)
        except GateABundleError as error:
            _write_json(
                {
                    "ok": False,
                    "error": {
                        "code": "windows_gate_a_error",
                        "message": str(error),
                    },
                },
                stream=sys.stderr,
            )
            return 5
        _write_json({"ok": True, "result": result})
        return 0
    if arguments.command == "verify-windows-gate-b-evidence":
        return _gate_b_evidence(arguments, attack=False)
    if arguments.command == "attack-windows-gate-b-evidence":
        return _gate_b_evidence(arguments, attack=True)
    if arguments.command == "memory-camu-add":
        return _memory_camu_add(arguments)
    if arguments.command == "memory-camu-list":
        return _memory_camu_list(arguments)
    if arguments.command == "memory-camu-show":
        return _memory_camu_show(arguments)
    if arguments.command == "memory-camu-outcome":
        return _memory_camu_outcome(arguments)
    if arguments.command == "memory-recall":
        return _memory_recall(arguments)
    if arguments.command == "memory-consolidate":
        return _memory_consolidate(arguments)
    if arguments.command == "memory-verify-chain":
        return _memory_verify_chain(arguments)
    if arguments.command == "loop-demo-run":
        return _loop_demo_run(arguments)
    if arguments.command == "autonomous-loop-smoke":
        return autonomous_loop_smoke_main(
            [
                "--fixture",
                arguments.fixture,
                "--output",
                str(arguments.output),
                "--promotion-passes",
                str(arguments.promotion_passes),
            ]
        )
    raise AssertionError("argparse accepted an unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
