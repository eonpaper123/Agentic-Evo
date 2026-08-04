from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from .adapters.codex import handle_codex_hook
from ._util import canonical_json_bytes
from .errors import AgenticEvoError
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
from .service import main as service_main
from .windows_gate_a import (
    GateABundleError,
    cleanup_gate_a_bundle,
    prepare_gate_a_bundle,
    verify_gate_a_bundle,
)


MAX_HOOK_INPUT_BYTES = 2 * 1024 * 1024
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


def _codex_hook(home: Path) -> int:
    payload = _read_hook_input()
    if payload is None:
        return 0
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
        ("hook", "Handle one bounded Codex lifecycle hook from stdin."),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--dev-home",
            type=Path,
            required=True,
            help="Existing disposable runtime home; never performs Genesis.",
        )
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
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "serve":
        return service_main(["--dev-home", str(arguments.dev_home)])
    if arguments.command == "status":
        return _surface_status(arguments.dev_home)
    if arguments.command == "off":
        return _off_rehearsal(arguments.dev_home)
    if arguments.command == "hook":
        return _codex_hook(arguments.dev_home)
    if arguments.command == "surface-stdio":
        return _surface_stdio(arguments.dev_home, arguments.execution_surface)
    if arguments.command == "plan-install":
        _write_json(build_install_plan())
        return 0
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
    raise AssertionError("argparse accepted an unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
