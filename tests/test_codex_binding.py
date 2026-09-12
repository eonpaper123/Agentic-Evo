from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentic_evo.codex_binding import (
    CODEX_BINDING_SCHEMA,
    CodexBindingError,
    bind_codex_installation,
    unbind_codex_installation,
)


class CodexBindingTests(unittest.TestCase):
    def _paths(self, root: Path) -> dict[str, Path]:
        program_dir = root / "program"
        program_dir.mkdir()
        (program_dir / "agentic-evo.pyz").write_bytes(b"formal-release")
        runtime_home = root / "runtime"
        runtime_home.mkdir()
        python_executable = root / "python.exe"
        python_executable.write_bytes(b"python")
        codex_executable = root / "codex.exe"
        codex_executable.write_bytes(b"codex")
        codex_home = root / "codex-home"
        codex_home.mkdir()
        return {
            "program_dir": program_dir,
            "runtime_home": runtime_home,
            "python_executable": python_executable,
            "codex_executable": codex_executable,
            "codex_home": codex_home,
        }

    @staticmethod
    def _quoted(path: Path) -> str:
        return '"' + str(path.resolve()).replace("\\", "/") + '"'

    def _old_command(self, paths: dict[str, Path]) -> str:
        legacy_pyz = paths["codex_home"] / "agentic-evo" / "agentic-evo.pyz"
        return " ".join(
            (
                self._quoted(paths["python_executable"]),
                self._quoted(legacy_pyz),
                "hook",
                "--surface",
                "codex",
                "--dev-home",
                self._quoted(paths["runtime_home"]),
                "--start-if-needed",
            )
        )

    def _old_windows_command(self, paths: dict[str, Path]) -> str:
        return self._quoted(paths["codex_home"] / "agentic-evo" / "agentic-evo-hook.cmd")

    def _write_legacy_binding(self, paths: dict[str, Path]) -> dict[str, Path]:
        legacy_dir = paths["codex_home"] / "agentic-evo"
        legacy_dir.mkdir()
        legacy_pyz = legacy_dir / "agentic-evo.pyz"
        legacy_pyz.write_bytes(b"old-copy")
        hook = legacy_dir / "agentic-evo-hook.cmd"
        cli = legacy_dir / "agentic-evo.cmd"
        run_codex = legacy_dir / "run-codex.cmd"
        hook.write_text(
            "@echo off\r\n"
            + self._old_command(paths)
            + "\r\nexit /b %errorlevel%\r\n",
            encoding="utf-8",
            newline="",
        )
        cli.write_text(
            "@echo off\r\n"
            + self._quoted(paths["python_executable"])
            + " "
            + self._quoted(legacy_pyz)
            + " %*\r\nexit /b %errorlevel%\r\n",
            encoding="utf-8",
            newline="",
        )
        run_codex.write_text(
            "@echo off\r\n"
            + self._quoted(paths["python_executable"])
            + " "
            + self._quoted(legacy_pyz)
            + " run-codex --dev-home "
            + self._quoted(paths["runtime_home"])
            + " --codex-executable "
            + self._quoted(paths["codex_executable"])
            + " %*\r\nexit /b %errorlevel%\r\n",
            encoding="utf-8",
            newline="",
        )
        hooks = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": self._old_command(paths),
                                "commandWindows": self._old_windows_command(paths),
                                "timeout": 30,
                            }
                        ]
                    }
                ],
                "Unrelated": [{"hooks": [{"type": "command", "command": "other"}]}],
            }
        }
        (paths["codex_home"] / "hooks.json").write_text(
            json.dumps(hooks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {"hook": hook, "cli": cli, "run_codex": run_codex}

    def _bind(self, paths: dict[str, Path], **extra: object) -> dict[str, object]:
        return bind_codex_installation(
            program_dir=paths["program_dir"],
            runtime_home=paths["runtime_home"],
            python_executable=paths["python_executable"],
            codex_home=paths["codex_home"],
            codex_executable=paths["codex_executable"],
            **extra,
        )

    def test_bind_migrates_only_exact_legacy_evo_entries_and_returns_owned_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            launchers = self._write_legacy_binding(paths)
            with patch(
                "agentic_evo.codex_binding.trust_installed_codex_hooks", return_value=11
            ) as trust:
                binding = self._bind(paths)

            self.assertEqual(binding["schema"], CODEX_BINDING_SCHEMA)
            self.assertEqual(binding["status"], "bound")
            self.assertEqual(binding["program_dir"], str(paths["program_dir"].resolve()))
            self.assertEqual(binding["runtime_home"], str(paths["runtime_home"].resolve()))
            self.assertEqual(binding["python_executable"], str(paths["python_executable"].resolve()))
            self.assertEqual(binding["codex_home"], str(paths["codex_home"].resolve()))
            self.assertEqual(binding["codex_executable"], str(paths["codex_executable"].resolve()))
            self.assertEqual(binding["pyz_path"], str((paths["program_dir"] / "agentic-evo.pyz").resolve()))
            json.dumps(binding)
            self.assertEqual(len(binding["shim_paths"]), 3)
            self.assertEqual(len(binding["migrated_launcher_paths"]), 3)
            self.assertEqual(binding["preserved_launcher_paths"], [])
            self.assertEqual(len(binding["hook_config"]["entries"]), 11)
            self.assertEqual(binding["hook_config"]["entries"][0]["event_name"], "SessionStart")
            self.assertEqual(trust.call_args.kwargs["codex_home"], paths["codex_home"].resolve())
            self.assertEqual(trust.call_args.kwargs["working_directory"], paths["program_dir"].resolve())

            formal_pyz = str((paths["program_dir"] / "agentic-evo.pyz").resolve()).replace("\\", "/")
            for launcher in launchers.values():
                text = launcher.read_text(encoding="utf-8")
                self.assertIn(formal_pyz, text)
                self.assertNotIn("agentic-evo/agentic-evo.pyz", text)

            configured = json.loads((paths["codex_home"] / "hooks.json").read_text(encoding="utf-8"))
            handler = configured["hooks"]["SessionStart"][0]["hooks"][0]
            self.assertIn(formal_pyz, handler["command"])
            self.assertEqual(handler["commandWindows"], self._old_windows_command(paths))
            self.assertEqual(
                configured["hooks"]["Unrelated"][0]["hooks"][0]["command"], "other"
            )

    def test_modified_non_hook_legacy_launcher_is_preserved_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            launchers = self._write_legacy_binding(paths)
            launchers["cli"].write_text("@echo user-owned\r\n", encoding="utf-8", newline="")

            with patch("agentic_evo.codex_binding.trust_installed_codex_hooks", return_value=11):
                binding = self._bind(paths)

            self.assertEqual(launchers["cli"].read_text(encoding="utf-8"), "@echo user-owned\n")
            self.assertEqual(binding["preserved_launcher_paths"], [str(launchers["cli"].resolve())])
            self.assertEqual(
                {entry["name"] for entry in binding["shim_paths"]}, {"hook", "run_codex"}
            )

    def test_modified_hook_launcher_fails_without_mutating_hooks_or_other_launchers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            launchers = self._write_legacy_binding(paths)
            launchers["hook"].write_text("@echo user-owned\r\n", encoding="utf-8", newline="")
            original_hooks = (paths["codex_home"] / "hooks.json").read_text(encoding="utf-8")
            original_cli = launchers["cli"].read_text(encoding="utf-8")

            with self.assertRaisesRegex(CodexBindingError, "hook_launcher_modified"):
                self._bind(paths)

            self.assertEqual((paths["codex_home"] / "hooks.json").read_text(encoding="utf-8"), original_hooks)
            self.assertEqual(launchers["cli"].read_text(encoding="utf-8"), original_cli)

    def test_bind_creates_missing_evo_hooks_without_a_legacy_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))

            with patch("agentic_evo.codex_binding.trust_installed_codex_hooks", return_value=11):
                binding = self._bind(paths)

            configured = json.loads((paths["codex_home"] / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(set(configured["hooks"]), {
                "PermissionRequest",
                "PostCompact",
                "PostToolUse",
                "PreCompact",
                "PreToolUse",
                "SessionEnd",
                "SessionStart",
                "Stop",
                "SubagentStart",
                "SubagentStop",
                "UserPromptSubmit",
            })
            self.assertEqual(len(binding["hook_config"]["entries"]), 11)
            self.assertTrue(all((paths["codex_home"] / "agentic-evo" / name).is_file() for name in (
                "agentic-evo-hook.cmd", "agentic-evo.cmd", "run-codex.cmd"
            )))

    def test_bind_appends_missing_evo_hooks_without_changing_other_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            hooks_path = paths["codex_home"] / "hooks.json"
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {"hooks": [{"type": "command", "command": "other"}]}
                            ]
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("agentic_evo.codex_binding.trust_installed_codex_hooks", return_value=11):
                self._bind(paths)

            configured = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(
                configured["hooks"]["SessionStart"][0]["hooks"][0],
                {"type": "command", "command": "other"},
            )
            self.assertEqual(len(configured["hooks"]["SessionStart"]), 2)

    def test_unbind_removes_exact_owned_migrations_but_preserves_later_user_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            launchers = self._write_legacy_binding(paths)
            with patch("agentic_evo.codex_binding.trust_installed_codex_hooks", return_value=11):
                binding = self._bind(paths)
            launchers["cli"].write_text("@echo user-owned\r\n", encoding="utf-8", newline="")

            receipt = unbind_codex_installation(binding)

            self.assertEqual(receipt["status"], "unbound")
            self.assertFalse(launchers["hook"].exists())
            self.assertFalse(launchers["run_codex"].exists())
            self.assertEqual(launchers["cli"].read_text(encoding="utf-8"), "@echo user-owned\n")
            self.assertEqual(receipt["preserved_modified_paths"], [str(launchers["cli"].resolve())])
            configured = json.loads((paths["codex_home"] / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(configured["hooks"]["SessionStart"], [])
            self.assertEqual(
                configured["hooks"]["Unrelated"][0]["hooks"][0]["command"], "other"
            )
            self.assertEqual(receipt["removed_hook_entries"], 11)

    def test_unbind_preserves_a_later_edited_owned_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            self._write_legacy_binding(paths)
            with patch("agentic_evo.codex_binding.trust_installed_codex_hooks", return_value=11):
                binding = self._bind(paths)
            hooks_path = paths["codex_home"] / "hooks.json"
            configured = json.loads(hooks_path.read_text(encoding="utf-8"))
            configured["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "user-command"
            hooks_path.write_text(json.dumps(configured, indent=2) + "\n", encoding="utf-8")

            receipt = unbind_codex_installation(binding)

            configured = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(
                configured["hooks"]["SessionStart"][0]["hooks"][0]["command"], "user-command"
            )
            self.assertEqual(receipt["removed_hook_entries"], 10)
            self.assertEqual(receipt["preserved_hook_entries"], 1)
            self.assertIn(str(hooks_path.resolve()), receipt["preserved_modified_paths"])

    def test_unbind_rejects_foreign_binding(self) -> None:
        with self.assertRaisesRegex(CodexBindingError, "invalid_binding"):
            unbind_codex_installation({"schema": "not-agentic-evo"})


if __name__ == "__main__":
    unittest.main()
