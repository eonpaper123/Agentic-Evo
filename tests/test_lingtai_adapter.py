from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentic_evo.adapters.lingtai import LingTaiRunError, run_lingtai_task


class LingTaiAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        environment = patch.dict(
            "agentic_evo.adapters.lingtai.os.environ",
            {"USERPROFILE": "C:\\Users\\test", "HOME": "C:\\Users\\test"},
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        root = Path(self.tempdir.name)
        self.home = root / "evo-home"
        self.home.mkdir()
        self.project = root / "project"
        self.project.mkdir()
        self.preset = root / "preset.json"
        self.preset.write_text(
            json.dumps({"manifest": {"llm": {"provider": "openai"}}}),
            encoding="utf-8",
        )

    @patch("agentic_evo.adapters.lingtai.subprocess.run")
    def test_runs_native_entry_with_explicit_external_boundaries(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "native_task_id": "task-native-1",
                    "native_run_id": "run-native-1",
                    "success": False,
                    "readable_final": "原生结果 ✅",
                }
            ),
            stderr="LingTai 原生日志 ✅",
        )

        with patch.dict(
            "agentic_evo.adapters.lingtai.os.environ",
            {"USERPROFILE": "C:\\Users\\1", "HOME": "C:\\Users\\1"},
            clear=True,
        ):
            result = run_lingtai_task(
                home=self.home,
                prompt="Inspect the target project.",
                working_dir=self.project,
                lingtai_python=Path(sys.executable).resolve(),
                preset=str(self.preset.resolve()),
            )

        self.assertEqual(result.native_task_id, "task-native-1")
        self.assertEqual(result.native_run_id, "run-native-1")
        self.assertFalse(result.success)
        self.assertEqual(result.readable_final, "原生结果 ✅")

        args, kwargs = run.call_args
        self.assertEqual(args[0][0], str(Path(sys.executable).resolve()))
        self.assertEqual(kwargs["cwd"], str(self.project.resolve()))
        request = json.loads(kwargs["input"])
        self.assertEqual(request["home"], str(self.home.resolve()))
        self.assertEqual(request["working_dir"], str(self.project.resolve()))
        self.assertEqual(request["preset"], str(self.preset.resolve()))
        self.assertEqual(request["prompt"], "Inspect the target project.")
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertNotIn("LINGTAI_TUI_DIR", kwargs["env"])
        self.assertEqual(kwargs["env"]["USERPROFILE"], "C:\\Users\\1")
        self.assertEqual(kwargs["env"]["HOME"], "C:\\Users\\1")

    @patch("agentic_evo.adapters.lingtai.subprocess.run")
    def test_binds_codex_pool_parent_only_for_codex_pool_preset(self, run) -> None:
        tui_dir = Path(self.tempdir.name) / "lingtai-tui"
        tui_dir.mkdir()
        pool = tui_dir / "codex-auth-pool.json"
        pool.write_text("{}", encoding="utf-8")
        codex_preset = Path(self.tempdir.name) / "codex-preset.json"
        codex_preset.write_text(
            json.dumps(
                {
                    "manifest": {
                        "llm": {
                            "provider": "codex-pool",
                            "codex_auth_pool_path": str(pool.resolve()),
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "native_task_id": "task-native-1",
                    "native_run_id": "run-native-1",
                    "success": False,
                    "readable_final": "",
                }
            ),
            stderr="",
        )

        with patch.dict(
            "agentic_evo.adapters.lingtai.os.environ",
            {"USERPROFILE": "C:\\Users\\1", "HOME": "C:\\Users\\1"},
            clear=True,
        ):
            run_lingtai_task(
                home=self.home,
                prompt="Inspect the target project.",
                working_dir=self.project,
                lingtai_python=Path(sys.executable).resolve(),
                preset=str(codex_preset.resolve()),
            )

        _, kwargs = run.call_args
        self.assertEqual(kwargs["env"]["LINGTAI_TUI_DIR"], str(tui_dir.resolve()))
        self.assertEqual(kwargs["env"]["USERPROFILE"], "C:\\Users\\1")
        self.assertEqual(kwargs["env"]["HOME"], "C:\\Users\\1")

    @patch("agentic_evo.adapters.lingtai.subprocess.run")
    def test_rejects_malformed_or_relative_codex_pool_preset(self, run) -> None:
        self.preset.write_text(
            json.dumps({"manifest": {"llm": {"provider": "codex"}}}),
            encoding="utf-8",
        )
        run.reset_mock()
        with self.assertRaises(LingTaiRunError):
            run_lingtai_task(
                home=self.home,
                prompt="Inspect the target project.",
                working_dir=self.project,
                lingtai_python=Path(sys.executable).resolve(),
                preset=str(self.preset.resolve()),
            )
        self.assertEqual(run.call_count, 0)

        self.preset.write_text(
            json.dumps(
                {
                    "manifest": {
                        "llm": {
                            "provider": "codex_pool",
                            "codex_auth_pool_path": "relative-pool.json",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        run.reset_mock()
        with self.assertRaises(LingTaiRunError):
            run_lingtai_task(
                home=self.home,
                prompt="Inspect the target project.",
                working_dir=self.project,
                lingtai_python=Path(sys.executable).resolve(),
                preset=str(self.preset.resolve()),
            )
        self.assertEqual(run.call_count, 0)

        self.preset.write_text(
            json.dumps({"manifest": {"llm": []}}), encoding="utf-8"
        )
        run.reset_mock()
        with self.assertRaises(LingTaiRunError):
            run_lingtai_task(
                home=self.home,
                prompt="Inspect the target project.",
                working_dir=self.project,
                lingtai_python=Path(sys.executable).resolve(),
                preset=str(self.preset.resolve()),
            )
        self.assertEqual(run.call_count, 0)

    def test_rejects_missing_or_relative_preset_at_public_boundary(self) -> None:
        with self.assertRaises(LingTaiRunError):
            run_lingtai_task(
                home=self.home,
                prompt="Inspect the target project.",
                working_dir=self.project,
                lingtai_python=Path(sys.executable).resolve(),
                preset=None,
            )

        with self.assertRaises(LingTaiRunError):
            run_lingtai_task(
                home=self.home,
                prompt="Inspect the target project.",
                working_dir=self.project,
                lingtai_python=Path(sys.executable).resolve(),
                preset="preset.json",
            )

    @patch("agentic_evo.adapters.lingtai.subprocess.run")
    def test_surfaces_native_process_failure_without_a_fallback(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=6, stdout="", stderr="native failure"
        )

        with self.assertRaisesRegex(LingTaiRunError, "exit code 6"):
            run_lingtai_task(
                home=self.home,
                prompt="Inspect the target project.",
                working_dir=self.project,
                lingtai_python=Path(sys.executable).resolve(),
                preset=str(self.preset.resolve()),
            )


if __name__ == "__main__":
    unittest.main()
