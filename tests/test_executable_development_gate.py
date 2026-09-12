from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentic_evo.errors import InvalidBodyError
from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.witness import WitnessCore


class ExecutableDevelopmentGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.home = Path(self.temporary.name) / "runtime-home"
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding="test-host",
            purpose_anchor="test-purpose",
            initial_body={"entrypoint.md": "text Body"},
            instrument_version="test",
            protocol_version="test",
        )
        self.witness = WitnessCore(self.runtime)

    def tearDown(self) -> None:
        self.witness.close()
        self.temporary.cleanup()

    @staticmethod
    def _lpac_module(available: bool) -> SimpleNamespace:
        return SimpleNamespace(
            is_lpac_body_runtime_available=lambda: available,
        )

    def test_python_descriptor_cannot_advance_without_the_lpac_runtime(self) -> None:
        status = self.runtime.status()
        lease = self.witness.open_current_body_session(expected_head=status.head)
        candidate = lease.prepare_successor(
            files={
                "entrypoint.md": "text activation",
                "develop.py": "def develop(context):\n    return {'action': 'no_change'}\n",
            },
            development_kind="python-development-v1",
            development_artifact="develop.py",
        )

        with patch.dict(
            sys.modules,
            {"agentic_evo.body_lpac": self._lpac_module(False)},
        ):
            with self.assertRaisesRegex(
                InvalidBodyError,
                "Windows LPAC Body runtime",
            ):
                lease.advance_head(candidate_head=candidate)
        lease.close()

    def test_explicit_null_descriptor_returns_a_python_body_to_text_mode(self) -> None:
        initial = self.runtime.status()
        first = self.witness.open_current_body_session(expected_head=initial.head)
        executable = first.prepare_successor(
            files={
                "entrypoint.md": "text activation",
                "develop.py": "def develop(context):\n    return {'action': 'no_change'}\n",
            },
            development_kind="python-development-v1",
            development_artifact="develop.py",
        )
        with patch.dict(
            sys.modules,
            {"agentic_evo.body_lpac": self._lpac_module(True)},
        ):
            first.advance_head(candidate_head=executable)

        second = self.witness.open_current_body_session(expected_head=executable)
        cleared = second.prepare_successor(
            files={"entrypoint.md": "text-only successor"},
            development_kind=None,
            development_artifact=None,
        )
        manifest = self.runtime.body_store.read_manifest(cleared)
        self.assertIsNone(manifest.development_kind)
        self.assertIsNone(manifest.development_artifact)
        with patch.dict(
            sys.modules,
            {"agentic_evo.body_lpac": self._lpac_module(False)},
        ):
            after = second.advance_head(candidate_head=cleared)
        self.assertEqual(after.head, cleared)


if __name__ == "__main__":
    unittest.main()
