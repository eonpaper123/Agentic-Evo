from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .adapters.codex import handle_codex_hook
from .errors import AgenticEvoError
from .install_plan import build_install_plan
from .ipc import OffRehearsalClient, SurfaceClient
from .service import main as service_main
from .windows_gate_a import (
    GateABundleError,
    cleanup_gate_a_bundle,
    prepare_gate_a_bundle,
    verify_gate_a_bundle,
)


MAX_HOOK_INPUT_BYTES = 2 * 1024 * 1024


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
    raise AssertionError("argparse accepted an unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
