from __future__ import annotations

import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPOSITORY_ROOT / "tools" / "build_release.py"


class ReleasePackageTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source"
        package = source / "agentic_evo"
        package.mkdir(parents=True)
        (source / "__main__.py").write_text(
            "from agentic_evo.version import VERSION\nprint(VERSION)\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("\n", encoding="utf-8")
        (package / "version.py").write_text('VERSION = "0.0.1"\n', encoding="utf-8")
        return source

    def test_builds_versioned_archive_with_portable_installers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "output"
            result = subprocess.run(
                [
                    sys.executable,
                    str(BUILDER),
                    "--source",
                    str(self._source(root)),
                    "--output-dir",
                    str(output),
                    "--version",
                    "1.2.3",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (root / "source" / "agentic_evo" / "version.py").read_text(
                    encoding="utf-8"
                ),
                'VERSION = "0.0.1"\n',
            )
            receipt = json.loads(result.stdout)
            self.assertTrue(receipt["ok"])
            archive = Path(receipt["archive"])
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(
                    sorted(bundle.namelist()),
                    [
                        "agentic-evo-1.2.3/README.txt",
                        "agentic-evo-1.2.3/agentic-evo.pyz",
                        "agentic-evo-1.2.3/install.cmd",
                        "agentic-evo-1.2.3/install.sh",
                        "agentic-evo-1.2.3/release.json",
                    ],
                )
                windows = bundle.read("agentic-evo-1.2.3/install.cmd").decode("utf-8")
                unix = bundle.read("agentic-evo-1.2.3/install.sh").decode("utf-8")
                readme = bundle.read("agentic-evo-1.2.3/README.txt").decode("utf-8")
                manifest = json.loads(
                    bundle.read("agentic-evo-1.2.3/release.json").decode("utf-8")
                )
                pyz_bytes = bundle.read("agentic-evo-1.2.3/agentic-evo.pyz")
            with zipfile.ZipFile(io.BytesIO(pyz_bytes)) as pyz:
                embedded_version = pyz.read("agentic_evo/version.py").decode("utf-8")
            pyz_path = root / "agentic-evo.pyz"
            pyz_path.write_bytes(pyz_bytes)
            reported_version = subprocess.run(
                [sys.executable, str(pyz_path)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertIn("python -c", windows)
            self.assertIn("sys.version_info >= (3, 12)", windows)
            self.assertIn("Python 3.12 or later", windows)
            self.assertIn("install --artifact", windows)
            self.assertIn("python -c", unix)
            self.assertIn("sys.version_info >= (3, 12)", unix)
            self.assertIn("install --artifact", unix)
            self.assertNotIn("py -3", windows)
            self.assertNotIn(str(sys.executable), windows)
            self.assertNotIn(str(sys.executable), unix)
            self.assertIn("--home <runtime-home> --codex-executable", readme)
            self.assertIn("--lingtai-python <absolute-lingtai-venv-python>", readme)
            self.assertIn("--lingtai-preset <absolute-existing-preset>", readme)
            self.assertIn("optional Evo LingTai harness", readme)
            self.assertIn(
                "Provide both --lingtai-python and --lingtai-preset to enable it",
                readme,
            )
            self.assertNotIn("Both --lingtai-python and --lingtai-preset are required", readme)
            self.assertNotIn("D:\\rawle\\.lingtai-tui", readme)
            self.assertIn("new or empty home creates an identity", readme)
            self.assertIn("--host-binding", readme)
            self.assertIn("Evo Codex.cmd on Windows", readme)
            self.assertIn("Evo LingTai.cmd is added only when enabled", readme)
            self.assertEqual(manifest["version"], "1.2.3")
            self.assertEqual(manifest["artifact"]["format"], "zipapp")
            self.assertEqual(
                manifest["daily_launchers"]["windows"],
                ["Evo Codex.cmd"],
            )
            self.assertEqual(
                manifest["optional_daily_launchers"]["windows"],
                ["Evo LingTai.cmd"],
            )
            self.assertEqual(embedded_version, "VERSION = '1.2.3'\n")
            self.assertEqual(reported_version.returncode, 0, reported_version.stderr)
            self.assertEqual(reported_version.stdout, "1.2.3\n")

    def test_refuses_to_overwrite_a_versioned_archive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = self._source(root)
            output = root / "output"
            arguments = [
                sys.executable,
                str(BUILDER),
                "--source",
                str(source),
                "--output-dir",
                str(output),
                "--version",
                "1.2.3",
            ]
            first = subprocess.run(arguments, capture_output=True, text=True, timeout=20)
            second = subprocess.run(arguments, capture_output=True, text=True, timeout=20)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 6)
            self.assertEqual(
                json.loads(second.stdout)["error"]["code"], "release_build_failed"
            )

    def test_project_archive_embeds_the_requested_build_version(self) -> None:
        source = REPOSITORY_ROOT / "src"
        original_version = (source / "agentic_evo" / "version.py").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "output"
            result = subprocess.run(
                [
                    sys.executable,
                    str(BUILDER),
                    "--source",
                    str(source),
                    "--output-dir",
                    str(output),
                    "--version",
                    "0.1.0-candidate.1",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            archive = Path(json.loads(result.stdout)["archive"])
            with zipfile.ZipFile(archive) as bundle:
                pyz_bytes = bundle.read(
                    "agentic-evo-0.1.0-candidate.1/agentic-evo.pyz"
                )
            with zipfile.ZipFile(io.BytesIO(pyz_bytes)) as pyz:
                embedded = pyz.read("agentic_evo/version.py").decode("utf-8")

        self.assertEqual(embedded, "VERSION = '0.1.0-candidate.1'\n")
        self.assertEqual(
            (source / "agentic_evo" / "version.py").read_text(encoding="utf-8"),
            original_version,
        )


if __name__ == "__main__":
    unittest.main()
