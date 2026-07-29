from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentic_evo._util import ExclusiveFileLock


class ExclusiveFileLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.tempdir.name) / "runtime.lock"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_lock_is_exclusive_and_reusable(self) -> None:
        with ExclusiveFileLock(self.lock_path):
            with self.assertRaises(TimeoutError):
                with ExclusiveFileLock(
                    self.lock_path,
                    timeout_seconds=0.05,
                ):
                    self.fail("a second writer acquired the same lock")

        with ExclusiveFileLock(self.lock_path, timeout_seconds=0.05):
            pass

    def test_process_death_releases_lock_without_stale_lock_repair(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src"
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(source_root)
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os, sys;"
                    "from pathlib import Path;"
                    "from agentic_evo._util import ExclusiveFileLock;"
                    "lock=ExclusiveFileLock(Path(sys.argv[1]));"
                    "lock.__enter__();"
                    "os._exit(0)"
                ),
                str(self.lock_path),
            ],
            env=environment,
            check=False,
            timeout=5,
        )
        self.assertEqual(child.returncode, 0)

        with ExclusiveFileLock(self.lock_path, timeout_seconds=0.2):
            pass


if __name__ == "__main__":
    unittest.main()
