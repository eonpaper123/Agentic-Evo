from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agentic_evo import cli
from agentic_evo.release_lifecycle import (
    InstallLayout,
    ReleaseLifecycleError,
    _ProtocolResult,
    install,
    installed_status,
    load_installed_layout,
    _runtime_launcher_text,
    uninstall,
    upgrade,
    validate_install_layout,
)


class ReleaseLifecycleTests(unittest.TestCase):
    def _layout(
        self, root: Path, *, version: str = "1.2.3", lingtai_enabled: bool = False
    ) -> InstallLayout:
        artifact = root / f"agentic-evo-{version}.pyz"
        artifact.write_bytes(f"release-{version}".encode("utf-8"))
        runtime_home = root / "existing-runtime-home"
        runtime_home.mkdir()
        return InstallLayout(
            program_dir=root / "program",
            data_dir=root / "data",
            runtime_home=runtime_home,
            release_artifact=artifact,
            python_executable=Path(sys.executable),
            version=version,
            lingtai_enabled=lingtai_enabled,
        )

    @staticmethod
    def _completed(
        *,
        code: int = 0,
        stdout: str = '{"ok": true}',
        stderr: str = "secret-from-runtime",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=code, stdout=stdout, stderr=stderr
        )

    @staticmethod
    def _versioned_artifact(path: Path, version: str) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("agentic_evo/version.py", f"VERSION = {version!r}\n")

    @staticmethod
    def _self_deleting_artifact(path: Path) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "__main__.py",
                "from pathlib import Path\n"
                "import os\n"
                "import shutil\n"
                "shutil.rmtree(Path(os.environ['EVO_TEST_PROGRAM_DIR']))\n"
                "raise SystemExit(int(os.environ['EVO_TEST_EXIT_CODE']))\n",
            )

    @staticmethod
    def _exit_artifact(path: Path, exit_code: int) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("__main__.py", f"raise SystemExit({exit_code})\n")

    @staticmethod
    def _argument_echo_artifact(path: Path) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "__main__.py",
                "import json\n"
                "import sys\n"
                "print(json.dumps(sys.argv[1:]))\n",
            )

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher semantics")
    def test_windows_runtime_launcher_binds_only_managed_runtime_commands(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            program_dir = root / "program"
            program_dir.mkdir()
            runtime_home = root / "runtime-home"
            runtime_home.mkdir()
            artifact = program_dir / "agentic-evo.pyz"
            self._argument_echo_artifact(artifact)
            layout = InstallLayout(
                program_dir=program_dir,
                data_dir=root / "data",
                runtime_home=runtime_home,
                release_artifact=artifact,
                python_executable=Path(sys.executable),
                version="test",
            )
            launcher = program_dir / "agentic-evo.cmd"
            launcher.write_text(_runtime_launcher_text(layout), encoding="utf-8")

            def invoke(*arguments: str) -> list[str]:
                completed = subprocess.run(
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "call "
                        + subprocess.list2cmdline([str(launcher), *arguments]),
                    ],
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
                return json.loads(completed.stdout)

            self.assertEqual(
                invoke("status"),
                ["status", "--home", str(runtime_home)],
            )
            self.assertEqual(
                invoke("status", "--home", str(root / "explicit-home")),
                ["status", "--home", str(root / "explicit-home")],
            )
            self.assertEqual(
                invoke("status", "--dev-home=" + str(root / "legacy-home")),
                ["status", "--dev-home=" + str(root / "legacy-home")],
            )
            self.assertEqual(
                invoke("on"),
                ["on", "--home", str(runtime_home)],
            )
            self.assertEqual(
                invoke("console", "--surface", "codex", "--cwd", str(root)),
                ["console", "--surface", "codex", "--cwd", str(root)],
            )
            self.assertEqual(
                invoke("install", "--artifact", "candidate.pyz"),
                ["install", "--artifact", "candidate.pyz"],
            )
            self.assertEqual(
                invoke("upgrade", "--artifact", "candidate.pyz"),
                ["upgrade", "--artifact", "candidate.pyz"],
            )
            launcher_text = launcher.read_text(encoding="utf-8")
            self.assertLess(
                launcher_text.index('if "%~1"=="uninstall" goto uninstall'),
                launcher_text.index("call :has_explicit_managed_home %*"),
            )

    def test_unix_runtime_launcher_limits_default_home_to_managed_commands(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            with patch("agentic_evo.release_lifecycle.os.name", "posix"):
                launcher = _runtime_launcher_text(layout)

            self.assertIn("status | on | off | recover | shutdown)", launcher)
            self.assertIn("--home|--home=*|--dev-home|--dev-home=*)", launcher)
            self.assertIn("--home " + shlex.quote(str(layout.runtime_home)), launcher)
            self.assertNotIn("console|lingtai", launcher)

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher semantics")
    def test_windows_runtime_launcher_returns_to_a_calling_cmd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            program_dir = root / "program"
            program_dir.mkdir()
            artifact = program_dir / "agentic-evo.pyz"
            self._exit_artifact(artifact, 0)
            layout = InstallLayout(
                program_dir=program_dir,
                data_dir=root / "data",
                runtime_home=root / "runtime-home",
                release_artifact=artifact,
                python_executable=Path(sys.executable),
                version="test",
            )
            launcher = program_dir / "agentic-evo.cmd"
            launcher.write_text(_runtime_launcher_text(layout), encoding="utf-8")

            completed = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    f'call "{launcher}" status & echo caller-survived',
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("caller-survived", completed.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher semantics")
    def test_windows_runtime_launcher_preserves_child_exit_after_self_deletion(self) -> None:
        for expected_exit in (0, 7):
            with self.subTest(expected_exit=expected_exit), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                program_dir = root / "program"
                program_dir.mkdir()
                runtime_home = root / "runtime-home"
                runtime_home.mkdir()
                artifact = program_dir / "agentic-evo.pyz"
                self._self_deleting_artifact(artifact)
                layout = InstallLayout(
                    program_dir=program_dir,
                    data_dir=root / "data",
                    runtime_home=runtime_home,
                    release_artifact=artifact,
                    python_executable=Path(sys.executable),
                    version="test",
                )
                launcher = program_dir / "agentic-evo.cmd"
                launcher.write_text(_runtime_launcher_text(layout), encoding="utf-8")
                environment = os.environ.copy()
                environment["EVO_TEST_PROGRAM_DIR"] = str(program_dir)
                environment["EVO_TEST_EXIT_CODE"] = str(expected_exit)

                completed = subprocess.run(
                    ["cmd.exe", "/d", "/c", str(launcher), "uninstall"],
                    cwd=root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )

                output = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, expected_exit, output)
                self.assertFalse(program_dir.exists())
                self.assertNotIn("The system cannot find the path specified.", output)

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher semantics")
    def test_windows_uninstall_launcher_returns_to_a_calling_cmd(self) -> None:
        for expected_exit in (0, 7):
            with self.subTest(expected_exit=expected_exit), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                program_dir = root / "program"
                program_dir.mkdir()
                artifact = program_dir / "agentic-evo.pyz"
                self._self_deleting_artifact(artifact)
                layout = InstallLayout(
                    program_dir=program_dir,
                    data_dir=root / "data",
                    runtime_home=root / "runtime-home",
                    release_artifact=artifact,
                    python_executable=Path(sys.executable),
                    version="test",
                )
                launcher = program_dir / "agentic-evo.cmd"
                launcher.write_text(_runtime_launcher_text(layout), encoding="utf-8")
                caller = root / "caller.cmd"
                caller.write_text(
                    "@echo off\r\n"
                    f'call "{launcher}" uninstall\r\n'
                    'set "EVO_TEST_CALL_RC=%ERRORLEVEL%"\r\n'
                    "echo caller-survived\r\n"
                    "exit /b %EVO_TEST_CALL_RC%\r\n",
                    encoding="utf-8",
                )
                environment = os.environ.copy()
                environment["EVO_TEST_PROGRAM_DIR"] = str(program_dir)
                environment["EVO_TEST_EXIT_CODE"] = str(expected_exit)

                completed = subprocess.run(
                    ["cmd.exe", "/d", "/c", str(caller)],
                    cwd=root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )

                output = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, expected_exit, output)
                self.assertIn("caller-survived", output)
                self.assertFalse(program_dir.exists())
                self.assertNotIn("The system cannot find the path specified.", output)

    def test_install_writes_versioned_payload_hidden_recovery_and_uses_cli_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch(
                    "agentic_evo.release_lifecycle._register_autostart"
                ) as register,
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ) as run,
            ):
                receipt = install(layout)

            self.assertTrue(receipt["ok"])
            self.assertTrue(receipt["installed"])
            self.assertEqual(
                receipt["daily_launchers"]["codex"],
                str(layout.program_dir / ("Evo Codex.cmd" if sys.platform == "win32" else "evo-codex")),
            )
            self.assertNotIn("lingtai", receipt["daily_launchers"])
            register.assert_called_once()
            self.assertEqual(
                run.call_args.args[0],
                [
                    str(Path(sys.executable).resolve()),
                    str((layout.program_dir / "agentic-evo.pyz").resolve()),
                    "recover",
                    "--home",
                    str(layout.runtime_home.resolve()),
                    "--restart",
                ],
            )
            manifest = json.loads(
                (layout.program_dir / "install-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["version"], "1.2.3")
            self.assertEqual(manifest["runtime_home"], str(layout.runtime_home.resolve()))
            self.assertFalse(manifest["lingtai_enabled"])
            self.assertNotIn("lingtai_launcher", manifest)
            self.assertEqual(
                (layout.program_dir / "agentic-evo.pyz").read_bytes(), b"release-1.2.3"
            )
            if sys.platform == "win32":
                recovery = (layout.program_dir / "agentic-evo-recover.vbs").read_text(
                    encoding="utf-8"
                )
                self.assertIn("shell.Run", recovery)
                self.assertIn(", 0, False", recovery)
                self.assertIn("recover", recovery)
                self.assertNotIn("--restart", recovery)
            console = (layout.program_dir / ("Evo Codex.cmd" if sys.platform == "win32" else "evo-codex")).read_text(
                encoding="utf-8"
            )
            self.assertIn("console --home", console)
            self.assertIn("--surface codex", console)
            if sys.platform == "win32":
                runtime_launcher = (
                    layout.program_dir / "agentic-evo.cmd"
                ).read_text(encoding="utf-8")
                self.assertIn("chcp 65001 >nul", runtime_launcher)
                self.assertIn('set "PYTHONUTF8=1"', runtime_launcher)
                self.assertIn(
                    'if not exist "%EVO_PROJECT_DIR%\\." goto choose_project',
                    console,
                )
                self.assertNotIn("%EVO_PROJECT_DIR%\\NUL", console)
            self.assertFalse(
                (
                    layout.program_dir
                    / ("Evo LingTai.cmd" if sys.platform == "win32" else "evo-lingtai")
                ).exists()
            )

    def test_install_with_lingtai_enabled_writes_the_optional_harness_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw), lingtai_enabled=True)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ),
            ):
                receipt = install(layout)

            launcher = layout.program_dir / (
                "Evo LingTai.cmd" if sys.platform == "win32" else "evo-lingtai"
            )
            manifest = json.loads(
                (layout.program_dir / "install-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["daily_launchers"]["lingtai"], str(launcher))
            self.assertTrue(manifest["lingtai_enabled"])
            self.assertEqual(manifest["lingtai_launcher"], launcher.name)
            lingtai = launcher.read_text(encoding="utf-8")
            self.assertIn("Enter an existing project directory", lingtai)
            self.assertIn("Agentic-Evo LingTai project", lingtai)
            self.assertIn("lingtai --home", lingtai)
            self.assertIn("--cwd", lingtai)
            self.assertNotIn("opencode", lingtai.lower())
            if sys.platform == "win32":
                self.assertIn(
                    'if not exist "%EVO_PROJECT_DIR%\\." goto choose_project',
                    lingtai,
                )
                self.assertNotIn("%EVO_PROJECT_DIR%\\NUL", lingtai)

    def test_upgrade_replaces_only_release_payload_and_preserves_identity_data(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self._layout(root, version="1.2.3", lingtai_enabled=True)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ) as run,
            ):
                self.assertTrue(install(first)["ok"])

                (first.data_dir / "history.txt").write_text("retain", encoding="utf-8")
                (first.runtime_home / "identity.txt").write_text("root", encoding="utf-8")
                second_artifact = root / "agentic-evo-1.2.4.pyz"
                second_artifact.write_bytes(b"release-1.2.4")
                second = InstallLayout(
                    program_dir=first.program_dir,
                    data_dir=first.data_dir,
                    runtime_home=first.runtime_home,
                    release_artifact=second_artifact,
                    python_executable=Path(sys.executable),
                    version="1.2.4",
                    lingtai_enabled=False,
                )
                receipt = upgrade(second)

            self.assertTrue(receipt["ok"])
            self.assertEqual(
                (first.program_dir / "agentic-evo.pyz").read_bytes(), b"release-1.2.4"
            )
            self.assertEqual(
                (first.data_dir / "history.txt").read_text(encoding="utf-8"), "retain"
            )
            self.assertEqual(
                (first.runtime_home / "identity.txt").read_text(encoding="utf-8"), "root"
            )
            self.assertFalse(
                (
                    first.program_dir
                    / ("Evo LingTai.cmd" if sys.platform == "win32" else "evo-lingtai")
                ).exists()
            )
            manifest = json.loads(
                (first.program_dir / "install-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(manifest["lingtai_enabled"])
            self.assertNotIn("lingtai_launcher", manifest)
            self.assertEqual(run.call_args.args[0][-1], "--restart")

    def test_upgrade_stops_the_old_payload_before_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self._layout(root, version="1.2.3")
            success = _ProtocolResult(0, {"ok": True}, None)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle._call_runtime_cli",
                    return_value=success,
                ),
            ):
                self.assertTrue(install(first)["ok"])

            replacement = root / "agentic-evo-1.2.4.pyz"
            replacement.write_bytes(b"release-1.2.4")
            second = InstallLayout(
                program_dir=first.program_dir,
                data_dir=first.data_dir,
                runtime_home=first.runtime_home,
                release_artifact=replacement,
                python_executable=first.python_executable,
                version="1.2.4",
            )

            def runtime_call(
                _layout: InstallLayout,
                command: str,
                *_extra: str,
            ) -> _ProtocolResult:
                if command == "shutdown":
                    self.assertEqual(
                        (first.program_dir / "agentic-evo.pyz").read_bytes(),
                        b"release-1.2.3",
                    )
                elif command == "recover":
                    self.assertEqual(
                        (first.program_dir / "agentic-evo.pyz").read_bytes(),
                        b"release-1.2.4",
                    )
                return success

            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle._call_runtime_cli",
                    side_effect=runtime_call,
                ) as runtime,
            ):
                receipt = upgrade(second)

            self.assertTrue(receipt["ok"])
            self.assertEqual(
                [call.args[1] for call in runtime.call_args_list],
                ["shutdown", "recover"],
            )

    def test_upgrade_does_not_replace_payload_when_shutdown_is_unconfirmed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self._layout(root, version="1.2.3")
            success = _ProtocolResult(0, {"ok": True}, None)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle._call_runtime_cli",
                    return_value=success,
                ),
            ):
                self.assertTrue(install(first)["ok"])

            replacement = root / "agentic-evo-1.2.4.pyz"
            replacement.write_bytes(b"release-1.2.4")
            second = InstallLayout(
                program_dir=first.program_dir,
                data_dir=first.data_dir,
                runtime_home=first.runtime_home,
                release_artifact=replacement,
                python_executable=first.python_executable,
                version="1.2.4",
            )
            original_manifest = (first.program_dir / "install-manifest.json").read_bytes()
            shutdown_failure = _ProtocolResult(6, None, "shutdown_failed")
            with (
                patch("agentic_evo.release_lifecycle._register_autostart") as register,
                patch(
                    "agentic_evo.release_lifecycle._call_runtime_cli",
                    return_value=shutdown_failure,
                ) as runtime,
            ):
                receipt = upgrade(second)

            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["error"], {"code": "shutdown_failed"})
            self.assertEqual(
                (first.program_dir / "agentic-evo.pyz").read_bytes(),
                b"release-1.2.3",
            )
            self.assertEqual(
                (first.program_dir / "install-manifest.json").read_bytes(),
                original_manifest,
            )
            self.assertEqual([call.args[1] for call in runtime.call_args_list], ["shutdown"])
            register.assert_not_called()

    def test_upgrade_accepts_a_runtime_proven_already_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self._layout(root, version="1.2.3")
            success = _ProtocolResult(0, {"ok": True}, None)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle._call_runtime_cli",
                    return_value=success,
                ),
            ):
                self.assertTrue(install(first)["ok"])

            replacement = root / "agentic-evo-1.2.4.pyz"
            replacement.write_bytes(b"release-1.2.4")
            second = InstallLayout(
                program_dir=first.program_dir,
                data_dir=first.data_dir,
                runtime_home=first.runtime_home,
                release_artifact=replacement,
                python_executable=first.python_executable,
                version="1.2.4",
            )
            already_stopped = _ProtocolResult(4, None, "service_not_running")
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle._call_runtime_cli",
                    side_effect=(already_stopped, success),
                ) as runtime,
            ):
                receipt = upgrade(second)

            self.assertTrue(receipt["ok"])
            self.assertEqual(
                [call.args[1] for call in runtime.call_args_list],
                ["shutdown", "recover"],
            )

    def test_cli_upgrade_uses_the_candidate_artifact_version(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            installed = self._layout(root, version="0.1.0-rc.5")
            candidate = root / "candidate-without-a-version-name.pyz"
            self._versioned_artifact(candidate, "0.1.0-rc.6")
            arguments = argparse.Namespace(
                program_dir=installed.program_dir,
                artifact=candidate,
                python_executable=Path(sys.executable),
            )
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ),
            ):
                self.assertTrue(install(installed)["ok"])
                with (
                    patch("agentic_evo.cli.VERSION", "0.1.0-rc.5"),
                    patch("agentic_evo.cli._write_json") as write_json,
                ):
                    self.assertEqual(cli._upgrade_product(arguments), 0)

            receipt = write_json.call_args.args[0]
            manifest = json.loads(
                (installed.program_dir / "install-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(receipt["version"], "0.1.0-rc.6")
            self.assertEqual(manifest["version"], "0.1.0-rc.6")
            self.assertEqual(
                (installed.program_dir / "agentic-evo.pyz").read_bytes(),
                candidate.read_bytes(),
            )

    def test_uninstall_default_retains_migratable_data_and_runtime_home(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch("agentic_evo.release_lifecycle._remove_autostart") as remove_startup,
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ) as run,
            ):
                self.assertTrue(install(layout)["ok"])
                (layout.data_dir / "history.txt").write_text("retain", encoding="utf-8")
                receipt = uninstall(layout)

            self.assertTrue(receipt["ok"])
            self.assertTrue(receipt["data_preserved"])
            self.assertFalse(layout.program_dir.exists())
            self.assertTrue(layout.data_dir.exists())
            self.assertTrue(layout.runtime_home.exists())
            remove_startup.assert_called_once()
            self.assertEqual(run.call_args_list[-2].args[0][2], "off")
            self.assertEqual(run.call_args.args[0][2], "shutdown")

    def test_explicit_purge_removes_data_and_runtime_home(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch("agentic_evo.release_lifecycle._remove_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ),
            ):
                self.assertTrue(install(layout)["ok"])
                (layout.data_dir / "history.txt").write_text("purge", encoding="utf-8")
                receipt = uninstall(layout, preserve_data=False)

            self.assertTrue(receipt["ok"])
            self.assertFalse(receipt["data_preserved"])
            self.assertFalse(layout.program_dir.exists())
            self.assertFalse(layout.data_dir.exists())
            self.assertFalse(layout.runtime_home.exists())

    def test_preflight_allows_a_new_runtime_home_but_rejects_unowned_data(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            layout.runtime_home.rmdir()

            preflight = validate_install_layout(layout)

            self.assertEqual(preflight.runtime_home, layout.runtime_home.resolve())
            self.assertFalse(preflight.runtime_home.exists())

            layout.data_dir.mkdir()
            (layout.data_dir / "unrelated-project-file.txt").write_text(
                "do not adopt", encoding="utf-8"
            )
            with self.assertRaisesRegex(ReleaseLifecycleError, "data_dir"):
                validate_install_layout(layout)

            second_root = root / "existing-program"
            second_root.mkdir()
            existing_program = self._layout(second_root)
            existing_program.program_dir.mkdir()
            with self.assertRaisesRegex(ReleaseLifecycleError, "program directory"):
                validate_install_layout(existing_program)

    def test_purge_refuses_an_unowned_existing_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ) as run,
            ):
                self.assertTrue(install(layout)["ok"])
                marker = layout.data_dir / ".agentic-evo-release-data.json"
                marker.unlink()
                unrelated = layout.data_dir / "unrelated-project-file.txt"
                unrelated.write_text("retain", encoding="utf-8")
                with self.assertRaisesRegex(ReleaseLifecycleError, "owned"):
                    uninstall(layout, preserve_data=False)

            self.assertTrue(layout.program_dir.exists())
            self.assertTrue(unrelated.exists())
            run.assert_called_once()

    def test_uninstall_allows_confirmed_stopped_witness_after_off_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch("agentic_evo.release_lifecycle._remove_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    side_effect=[
                        self._completed(),
                        self._completed(
                            code=4,
                            stdout="",
                            stderr=(
                                '{"ok":false,"error":{"code":"control_unavailable"}}'
                            ),
                        ),
                        self._completed(
                            code=4,
                            stdout="",
                            stderr=(
                                '{"ok":false,"error":{"code":"service_not_running"}}'
                            ),
                        ),
                        self._completed(
                            stdout=(
                                '{"ok":true,"result":{"already_stopped":true,"stopped":true}}'
                            )
                        ),
                    ],
                ) as run,
            ):
                self.assertTrue(install(layout)["ok"])
                receipt = uninstall(layout)

            self.assertTrue(receipt["ok"])
            self.assertFalse(layout.program_dir.exists())
            self.assertEqual(
                [call.args[0][2] for call in run.call_args_list],
                ["recover", "off", "status", "shutdown"],
            )
            self.assertEqual(
                receipt["runtime"]["off"]["status"]["error"]["code"],
                "control_unavailable",
            )
            self.assertEqual(
                receipt["runtime"]["status"]["status"]["error"]["code"],
                "service_not_running",
            )

    def test_uninstall_refuses_off_failure_without_stopped_proof(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch("agentic_evo.release_lifecycle._remove_autostart") as remove_startup,
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    side_effect=[
                        self._completed(),
                        self._completed(
                            code=4,
                            stdout="",
                            stderr=(
                                '{"ok":false,"error":{"code":"control_unavailable"}}'
                            ),
                        ),
                        self._completed(
                            code=6,
                            stdout="",
                            stderr=(
                                '{"ok":false,"error":{"code":"service_status_failed"}}'
                            ),
                        ),
                    ],
                ) as run,
            ):
                self.assertTrue(install(layout)["ok"])
                receipt = uninstall(layout)

            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["error"]["code"], "runtime_off_unconfirmed")
            self.assertEqual(
                receipt["runtime"]["status"]["status"]["error"]["code"],
                "service_status_failed",
            )
            self.assertTrue(layout.program_dir.exists())
            remove_startup.assert_not_called()
            self.assertEqual(
                [call.args[0][2] for call in run.call_args_list],
                ["recover", "off", "status"],
            )

    def test_uninstall_accepts_shutdown_unavailable_only_after_status_confirms_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch("agentic_evo.release_lifecycle._remove_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    side_effect=[
                        self._completed(),
                        self._completed(),
                        self._completed(code=4, stdout="", stderr='{"ok":false,"error":{"code":"shutdown_failed"}}'),
                        self._completed(code=4, stdout="", stderr='{"ok":false,"error":{"code":"service_not_running"}}'),
                    ],
                ) as run,
            ):
                self.assertTrue(install(layout)["ok"])
                receipt = uninstall(layout)

            self.assertTrue(receipt["ok"])
            self.assertFalse(layout.program_dir.exists())
            self.assertEqual(run.call_args_list[-2].args[0][2], "shutdown")
            self.assertEqual(run.call_args.args[0][2], "status")

    def test_uninstall_refuses_to_delete_when_shutdown_is_not_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch("agentic_evo.release_lifecycle._remove_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    side_effect=[
                        self._completed(),
                        self._completed(),
                        self._completed(code=4),
                        self._completed(),
                    ],
                ),
            ):
                self.assertTrue(install(layout)["ok"])
                receipt = uninstall(layout)

            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["error"]["code"], "runtime_shutdown_unconfirmed")
            self.assertTrue(layout.program_dir.exists())

    def test_load_installed_layout_preserves_manifest_identity_and_overrides_release(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ),
            ):
                self.assertTrue(install(layout)["ok"])
                next_artifact = root / "agentic-evo-1.2.4.pyz"
                self._versioned_artifact(next_artifact, "1.2.4")
                loaded = load_installed_layout(
                    layout.program_dir,
                    release_artifact=next_artifact,
                    python_executable=Path(sys.executable),
                )

            self.assertEqual(loaded.program_dir, layout.program_dir.resolve())
            self.assertEqual(loaded.data_dir, layout.data_dir.resolve())
            self.assertEqual(loaded.runtime_home, layout.runtime_home.resolve())
            self.assertEqual(loaded.release_artifact, next_artifact.resolve())
            self.assertEqual(loaded.version, "1.2.4")

    def test_status_reports_missing_install_without_touching_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            with patch("agentic_evo.release_lifecycle.subprocess.run") as run:
                receipt = installed_status(layout)
            self.assertTrue(receipt["ok"])
            self.assertFalse(receipt["installed"])
            run.assert_not_called()

    def test_upgrade_rejects_a_replacement_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            layout = self._layout(root)
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(),
                ),
            ):
                self.assertTrue(install(layout)["ok"])
                replacement = root / "replacement-runtime-home"
                replacement.mkdir()
                invalid = InstallLayout(
                    program_dir=layout.program_dir,
                    data_dir=layout.data_dir,
                    runtime_home=replacement,
                    release_artifact=layout.release_artifact,
                    python_executable=layout.python_executable,
                    version="1.2.4",
                )
                with self.assertRaises(ReleaseLifecycleError):
                    upgrade(invalid)

    def test_failed_recovery_logs_only_technical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            with (
                patch("agentic_evo.release_lifecycle._register_autostart"),
                patch(
                    "agentic_evo.release_lifecycle.subprocess.run",
                    return_value=self._completed(code=6, stdout="secret-from-runtime"),
                ),
            ):
                receipt = install(layout)

            self.assertFalse(receipt["ok"])
            log = (layout.data_dir / "logs" / "release-lifecycle.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("runtime_command_failed", log)
            self.assertNotIn("secret-from-runtime", log)
            self.assertNotIn(str(layout.runtime_home), log)


if __name__ == "__main__":
    unittest.main()
