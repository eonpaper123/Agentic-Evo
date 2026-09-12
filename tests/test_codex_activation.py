from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentic_evo.activation import (
    CodexHookTrustError,
    _CodexHookTrustProtocol,
    select_exact_codex_hooks,
    trust_installed_codex_hooks,
    trusted_hash_edits,
    verify_exact_codex_hook_trust,
)
from agentic_evo.adapters.codex import CodexHookError
from agentic_evo.cli import (
    CODEX_HOOK_EVENTS,
    _activate_codex,
    _codex_hook_command,
    _codex_windows_hook_command,
    _installed_status,
    _install_codex_hooks,
    _surface_status,
    _surface_hook,
    _write_pyz,
    _write_windows_cli_launcher,
    _write_windows_run_codex_launcher,
    _write_json,
    main,
)
from agentic_evo.ipc import ServiceNotRunningError, ServiceUnavailableError
from agentic_evo.runtime import DevelopmentalRuntime


_SOURCE = r"D:\rawle\.codex\hooks.json"
_COMMAND = '"D:/Python/python.exe" "D:/rawle/.codex/agentic-evo.pyz" hook --surface codex'
_HASH = "sha256:" + "a" * 64
_REQUIRED_EVENT_NAMES = frozenset(
    {
        "permissionRequest",
        "postCompact",
        "postToolUse",
        "preCompact",
        "preToolUse",
        "sessionEnd",
        "sessionStart",
        "stop",
        "subagentStart",
        "subagentStop",
        "userPromptSubmit",
    }
)


def _hook(
    *,
    key: str,
    current_hash: str = _HASH,
    trust_status: str = "untrusted",
    source_path: str = _SOURCE,
    command: str = _COMMAND,
    enabled: bool = True,
    event_name: str = "sessionStart",
) -> dict[str, object]:
    return {
        "key": key,
        "currentHash": current_hash,
        "trustStatus": trust_status,
        "sourcePath": source_path,
        "command": command,
        "enabled": enabled,
        "eventName": event_name,
    }


def _required_hooks(*, trust_status: str) -> list[dict[str, object]]:
    return [
        _hook(key=f"evo-{event_name}", event_name=event_name, trust_status=trust_status)
        for event_name in sorted(_REQUIRED_EVENT_NAMES)
    ]


class CodexActivationTests(unittest.TestCase):
    def test_trust_protocol_starts_codex_with_the_explicit_codex_home(self) -> None:
        process = MagicMock()
        process.stdin = io.StringIO()
        process.stdout = io.StringIO()
        process.poll.return_value = 0
        codex_home = Path(r"D:\rawle\.codex")
        with (
            patch("agentic_evo.activation.subprocess.Popen", return_value=process) as popen,
            patch.object(_CodexHookTrustProtocol, "_call", return_value={}),
        ):
            with _CodexHookTrustProtocol(
                codex_executable=Path(r"D:\rawle\Apps\CodexCLI\codex.exe"),
                codex_home=codex_home,
                working_directory=Path(r"D:\rawle\Coding\Agentic-Evo"),
            ):
                pass

        self.assertEqual(popen.call_args.kwargs["env"]["CODEX_HOME"], str(codex_home))

    def test_status_reports_only_absent_endpoint_as_not_running(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "agentic_evo.cli.SurfaceClient.status",
                side_effect=ServiceNotRunningError("absent"),
            ),
            patch("sys.stderr", output),
        ):
            returncode = _surface_status(Path("D:/runtime"))

        self.assertEqual(returncode, 4)
        self.assertEqual(
            json.loads(output.getvalue())["error"]["code"],
            "service_not_running",
        )

    def test_status_does_not_treat_protocol_failure_as_stopped(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "agentic_evo.cli.SurfaceClient.status",
                side_effect=ServiceUnavailableError("invalid response"),
            ),
            patch("sys.stderr", output),
        ):
            returncode = _surface_status(Path("D:/runtime"))

        self.assertEqual(returncode, 6)
        self.assertEqual(
            json.loads(output.getvalue())["error"]["code"],
            "service_status_failed",
        )

    def test_installed_pyz_propagates_cli_exit_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "agentic-evo.pyz"
            _write_pyz(Path(__file__).resolve().parents[1] / "src", target)
            with zipfile.ZipFile(target) as archive:
                entrypoint = archive.read("__main__.py").decode("utf-8")

        self.assertIn("raise SystemExit(main())", entrypoint)

    def test_windows_run_codex_launcher_binds_this_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "run-codex.cmd"
            _write_windows_run_codex_launcher(
                root / "agentic-evo.pyz",
                Path(r"D:\runtime"),
                Path(r"D:\apps\codex.exe"),
                launcher,
            )
            text = launcher.read_text(encoding="utf-8")

        self.assertIn("run-codex", text)
        self.assertIn('--dev-home "D:/runtime"', text)
        self.assertIn('--codex-executable "D:/apps/codex.exe"', text)
        self.assertTrue(text.rstrip().endswith("exit /b %errorlevel%"))

    def test_windows_cli_launcher_forwards_user_arguments_to_the_installed_pyz(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pyz = root / "agentic-evo.pyz"
            launcher = root / "agentic-evo.cmd"

            _write_windows_cli_launcher(pyz, launcher)

            text = launcher.read_text(encoding="utf-8")
        self.assertIn('"' + str(pyz).replace("\\", "/") + '" %*', text)
        self.assertIn("exit /b %errorlevel%", text)

    def test_cli_json_preserves_unicode_on_an_ascii_output_pipe(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii")

        _write_json({"message": "中文🙂"}, stream=stream)
        stream.flush()

        self.assertEqual(json.loads(raw.getvalue()), {"message": "中文🙂"})

    def test_windows_hook_command_is_a_single_quoted_launcher_path(self) -> None:
        command = _codex_windows_hook_command(
            Path(r"D:\rawle\.codex\agentic-evo\agentic-evo-hook.cmd")
        )

        self.assertEqual(
            command,
            '"D:/rawle/.codex/agentic-evo/agentic-evo-hook.cmd"',
        )

    def test_installed_hooks_use_the_windows_launcher_and_real_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            portable = _codex_hook_command(
                Path("/opt/agentic-evo/agentic-evo.pyz"),
                Path("/var/lib/agentic-evo"),
            )
            windows = _codex_windows_hook_command(
                Path(r"D:\rawle\.codex\agentic-evo\agentic-evo-hook.cmd")
            )

            added = _install_codex_hooks(codex_home, portable, windows)
            configured = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(added, len(_REQUIRED_EVENT_NAMES))
        handlers = {
            event: configured["hooks"][event][0]["hooks"][0]
            for event in CODEX_HOOK_EVENTS
        }
        self.assertTrue(all(handler["commandWindows"] == windows for handler in handlers.values()))
        self.assertEqual(handlers["SessionStart"]["timeout"], 30)
        self.assertEqual(handlers["SessionEnd"]["timeout"], 3)

    def test_trust_selection_only_includes_this_enabled_command_at_this_source(self) -> None:
        target = _hook(key=r"D:\rawle\.codex\hooks.json:session_start:1:0")
        selected = select_exact_codex_hooks(
            [
                target,
                _hook(
                    key="other-command",
                    command="python unrelated-hook.py",
                ),
                _hook(key="other-source", source_path=r"C:\rawle\.codex\hooks.json"),
                _hook(key="disabled", enabled=False),
            ],
            source_path=_SOURCE,
            command=_COMMAND,
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].key, target["key"])
        self.assertEqual(
            trusted_hash_edits(selected),
            [
                {
                    "keyPath": (
                        'hooks.state."D:\\\\rawle\\\\.codex\\\\hooks.json:'
                        'session_start:1:0".trusted_hash'
                    ),
                    "mergeStrategy": "replace",
                    "value": _HASH,
                }
            ],
        )

    def test_fresh_hook_list_must_keep_exact_hashes_and_report_trusted(self) -> None:
        expected = select_exact_codex_hooks(
            [_hook(key="evo-session-start")],
            source_path=_SOURCE,
            command=_COMMAND,
        )

        verify_exact_codex_hook_trust(
            [_hook(key="evo-session-start", trust_status="trusted")],
            expected=expected,
            source_path=_SOURCE,
            command=_COMMAND,
        )

        with self.assertRaises(CodexHookTrustError):
            verify_exact_codex_hook_trust(
                [_hook(key="evo-session-start", trust_status="untrusted")],
                expected=expected,
                source_path=_SOURCE,
                command=_COMMAND,
            )

    def test_activation_writes_only_selected_hashes_then_checks_fresh_list(self) -> None:
        before = _required_hooks(trust_status="untrusted")
        after = _required_hooks(trust_status="trusted")
        config_path = Path(r"D:\rawle\.codex\config.toml")
        source_path = Path(_SOURCE)

        with patch("agentic_evo.activation._CodexHookTrustProtocol") as protocol_type:
            protocol = protocol_type.return_value.__enter__.return_value
            protocol.list_hooks.side_effect = [before, after]

            count = trust_installed_codex_hooks(
                codex_executable=Path(r"D:\rawle\Apps\CodexCLI\codex.exe"),
                codex_home=Path(r"D:\rawle\.codex"),
                working_directory=Path(r"D:\rawle\Coding\Agentic-Evo"),
                config_path=config_path,
                source_path=source_path,
                command=_COMMAND,
            )

        self.assertEqual(count, len(_REQUIRED_EVENT_NAMES))
        protocol.list_hooks.assert_called_with(Path(r"D:\rawle\Coding\Agentic-Evo"))
        protocol.write_trusted_hashes.assert_called_once_with(
            trusted_hash_edits(select_exact_codex_hooks(before, source_path=source_path, command=_COMMAND)),
            config_path=config_path,
        )

    def test_activation_refuses_an_incomplete_required_hook_set(self) -> None:
        with patch("agentic_evo.activation._CodexHookTrustProtocol") as protocol_type:
            protocol = protocol_type.return_value.__enter__.return_value
            protocol.list_hooks.return_value = [_hook(key="evo-session-start")]

            with self.assertRaises(CodexHookTrustError):
                trust_installed_codex_hooks(
                    codex_executable=Path(r"D:\rawle\Apps\CodexCLI\codex.exe"),
                    codex_home=Path(r"D:\rawle\.codex"),
                    working_directory=Path(r"D:\rawle\Coding\Agentic-Evo"),
                    config_path=Path(r"D:\rawle\.codex\config.toml"),
                    source_path=Path(_SOURCE),
                    command=_COMMAND,
                )

        protocol.write_trusted_hashes.assert_not_called()

    def test_activate_codex_cannot_report_ready_before_live_trust_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "runtime"
            DevelopmentalRuntime.genesis(
                home,
                host_binding="test-host-binding",
                purpose_anchor="Improve the future of the one bound host.",
                initial_body={"entrypoint.md": "Body zero"},
                instrument_version="instrument-test-v1",
                protocol_version="protocol-test-v1",
            )
            codex_home = root / "codex-home"
            arguments = argparse.Namespace(
                home=home,
                install_root=root / "install",
                codex_home=codex_home,
                codex_executable=Path(r"D:\rawle\Apps\CodexCLI\codex.exe"),
            )

            with (
                patch("agentic_evo.cli._write_pyz"),
                patch("agentic_evo.cli._install_codex_hooks", return_value=11),
                patch("agentic_evo.cli._restart_existing_witness") as restart,
                patch("agentic_evo.cli._installed_status", return_value={"root": "root-a", "head": "head-a"}),
                patch("agentic_evo.cli.trust_installed_codex_hooks", return_value=11) as trust,
                patch("agentic_evo.cli._write_json"),
            ):
                result = _activate_codex(arguments)

        self.assertEqual(result, 0)
        self.assertEqual(trust.call_args.kwargs["codex_executable"], arguments.codex_executable)
        self.assertEqual(trust.call_args.kwargs["config_path"], codex_home / "config.toml")
        self.assertEqual(trust.call_args.kwargs["source_path"], codex_home / "hooks.json")
        restart.assert_called_once_with(home)

    def test_hook_startup_failure_is_visible_to_codex(self) -> None:
        with (
            patch(
                "agentic_evo.cli._read_hook_input",
                return_value={"hook_event_name": "SessionStart"},
            ),
            patch("agentic_evo.cli.ensure_witness", side_effect=RuntimeError("unavailable")),
            patch("agentic_evo.cli._write_json") as write_json,
        ):
            result = main(
                [
                    "hook",
                    "--surface",
                    "codex",
                    "--dev-home",
                    "D:/unavailable-runtime",
                    "--start-if-needed",
                ]
            )

        self.assertNotEqual(result, 0)
        self.assertEqual(write_json.call_args.args[0]["error"]["code"], "witness_start_error")

    def test_non_session_hook_does_not_check_witness_before_dispatch(self) -> None:
        payload = {"hook_event_name": "PostToolUse"}
        with (
            patch("agentic_evo.cli._read_hook_input", return_value=payload),
            patch("agentic_evo.cli.ensure_witness") as ensure,
            patch("agentic_evo.cli.handle_codex_hook", return_value=None) as handle,
        ):
            result = main(
                [
                    "hook",
                    "--surface",
                    "codex",
                    "--dev-home",
                    "D:/runtime",
                    "--start-if-needed",
                ]
            )

        self.assertEqual(result, 0)
        ensure.assert_not_called()
        handle.assert_called_once_with(Path("D:/runtime"), payload)

    def test_installed_status_starts_witness_directly(self) -> None:
        expected = {"root": "root-a", "head": "head-a"}
        with patch("agentic_evo.cli.ensure_witness", return_value=expected) as ensure:
            result = _installed_status(Path("D:/runtime"))

        self.assertEqual(result, expected)
        ensure.assert_called_once_with(Path("D:/runtime"))

    def test_recall_experiences_command_forwards_the_cursor(self) -> None:
        recalled = {
            "experiences": [{"sequence": 8, "payload": {"prompt": "prior task"}}],
            "has_more": True,
            "next_before_sequence": 8,
        }
        with (
            patch("agentic_evo.cli.SurfaceClient") as surface_client,
            patch("agentic_evo.cli._write_json") as write_json,
        ):
            surface_client.return_value.recall_experiences.return_value = recalled
            result = main(
                [
                    "recall-experiences",
                    "--dev-home",
                    "D:/runtime",
                    "--surface",
                    "codex",
                    "--session-id",
                    "current-session",
                    "--limit",
                    "3",
                    "--before-sequence",
                    "12",
                ]
            )

        self.assertEqual(result, 0)
        surface_client.return_value.recall_experiences.assert_called_once_with(
            execution_surface="codex",
            session_id="current-session",
            limit=3,
            before_sequence=12,
        )
        self.assertEqual(write_json.call_args.args[0], {"ok": True, "result": recalled})

    def test_surface_hook_runtime_failure_is_visible_and_nonzero(self) -> None:
        with (
            patch("agentic_evo.cli._read_hook_input", return_value={}),
            patch("agentic_evo.cli.handle_codex_hook", side_effect=CodexHookError("hidden")),
            patch("agentic_evo.cli._write_json") as write_json,
        ):
            result = _surface_hook(Path("D:/runtime"), "codex")

        self.assertEqual(result, 6)
        self.assertEqual(write_json.call_args.args[0]["error"]["code"], "codex_hook_error")


if __name__ == "__main__":
    unittest.main()
