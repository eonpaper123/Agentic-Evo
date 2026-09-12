from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "win32", "LPAC staging is Windows-only")
class BodyLpacStagingTests(unittest.TestCase):
    def test_staging_parent_uses_the_configured_absolute_runtime_location(
        self,
    ) -> None:
        from agentic_evo.body_lpac import _evo_staging_parent

        configured = Path("E:/configured-agentic-evo/.lpac-body-staging")
        with patch.object(Path, "mkdir") as mkdir:
            resolved = _evo_staging_parent(configured)

        self.assertEqual(resolved, configured.resolve())
        mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_ephemeral_profile_stages_package_resources_and_clean_stdlib(
        self,
    ) -> None:
        from agentic_evo.body_lpac import (
            BODY_LPAC_WORKER_MODULES,
            create_ephemeral_lpac_profile,
            stage_lpac_body_payload,
        )

        staging_root = Path.cwd() / "artifacts" / "lpac-test-staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=staging_root) as temporary:
            profile = create_ephemeral_lpac_profile()
            stage = None
            try:
                stage = stage_lpac_body_payload(
                    profile,
                    staging_parent=Path(temporary),
                )
                package = stage.payload_root / "agentic_evo"

                self.assertTrue(profile.name.startswith("agentic-evo-body-"))
                self.assertTrue(profile.sid)
                self.assertTrue(profile.sid_string.startswith("S-1-15-2-"))
                self.assertTrue(stage.scratch_path.is_dir())
                self.assertTrue(stage.python_executable.is_file())
                self.assertTrue((stage.runtime_root / "Lib" / "encodings").is_dir())
                self.assertTrue((stage.runtime_root / "Lib" / "fractions.py").is_file())
                self.assertTrue((stage.runtime_root / "Lib" / "zipfile").is_dir())
                self.assertFalse((package / "__init__.py").exists())
                self.assertFalse((package / "runtime.py").exists())
                self.assertFalse((package / "witness.py").exists())
                self.assertFalse((package / "site-packages").exists())
                self.assertFalse((stage.runtime_root / "Lib" / "site-packages").exists())
                self.assertFalse((stage.runtime_root / "Lib" / "ensurepip").exists())
                self.assertFalse((stage.runtime_root / "Lib" / "sitecustomize.py").exists())
                self.assertFalse((stage.runtime_root / "Lib" / "usercustomize.py").exists())
                self.assertFalse(
                    any(path.name == "__pycache__" for path in stage.runtime_root.rglob("*"))
                )
                self.assertEqual(
                    {path.name for path in package.glob("*.py")},
                    set(BODY_LPAC_WORKER_MODULES),
                )
            finally:
                if stage is not None:
                    root = stage.root_path
                    stage.close()
                    self.assertFalse(root.exists())
                else:
                    profile.close()
