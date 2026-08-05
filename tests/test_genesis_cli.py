from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentic_evo._util import sha256_hex
from agentic_evo.errors import AuthorityError
from agentic_evo.trusted import TrustedState


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"

HOST_BINDING = "host-alpha-machine"
PURPOSE_ANCHOR = "Improve the future of the one bound host."
ROOT = "ab" * 32
INITIAL_HEAD = "cd" * 32


class GenesisCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "identity-home"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        prior = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(SOURCE_ROOT)
            if not prior
            else os.pathsep.join((str(SOURCE_ROOT), prior))
        )
        return environment

    def _run_genesis(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                "genesis",
                "--home",
                str(self.home),
                "--host-binding",
                HOST_BINDING,
                "--purpose-anchor",
                PURPOSE_ANCHOR,
                "--root",
                ROOT,
                "--initial-head",
                INITIAL_HEAD,
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def _assert_success_receipt(
        self, completed: subprocess.CompletedProcess[str]
    ) -> dict[str, object]:
        self.assertEqual(completed.returncode, 0, completed.stderr)
        value = json.loads(completed.stdout)
        self.assertIsInstance(value, dict)
        return value

    def test_fresh_home_genesis_exit_zero_receipt_and_state_row(self) -> None:
        completed = self._run_genesis()
        value = self._assert_success_receipt(completed)
        self.assertEqual(value["ok"], True)
        result = value["result"]
        self.assertIsInstance(result, dict)
        self.assertEqual(
            set(result),
            {"who", "why", "root", "head", "authority", "evidence_ref", "home"},
        )
        self.assertEqual(result["who"], sha256_hex(HOST_BINDING))
        self.assertEqual(result["why"], sha256_hex(PURPOSE_ANCHOR))
        self.assertEqual(result["root"], ROOT)
        self.assertEqual(result["head"], INITIAL_HEAD)
        self.assertEqual(result["authority"], "on")
        self.assertEqual(result["home"], str(self.home))
        self.assertTrue((self.home / "state.sqlite3").is_file())
        self.assertTrue((self.home / "witness.key").is_file())

        trusted = TrustedState.load(self.home)
        snapshot = trusted.snapshot()
        self.assertEqual(snapshot.who, sha256_hex(HOST_BINDING))
        self.assertEqual(snapshot.why, sha256_hex(PURPOSE_ANCHOR))
        self.assertEqual(snapshot.root, ROOT)
        self.assertEqual(snapshot.head, INITIAL_HEAD)
        self.assertEqual(snapshot.authority, "on")

    def test_genesis_evidence_recorded_and_receipt_ref_matches(self) -> None:
        completed = self._run_genesis()
        value = self._assert_success_receipt(completed)
        trusted = TrustedState.load(self.home)
        records = trusted.records()
        self.assertGreaterEqual(len(records), 1)
        genesis_record = records[0]
        self.assertEqual(genesis_record.event_kind, "genesis")
        ref = value["result"]["evidence_ref"]
        self.assertIsInstance(ref, dict)
        self.assertEqual(ref["sequence"], genesis_record.sequence)
        self.assertEqual(ref["event_id"], genesis_record.event_id)
        self.assertEqual(ref["integrity_hash"], genesis_record.integrity_hash)

    def test_second_genesis_refused_exit_6_and_state_unchanged(self) -> None:
        first = self._run_genesis()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = TrustedState.load(self.home)
        before_snapshot = before.snapshot()
        before_records = before.records()

        second = self._run_genesis()
        self.assertEqual(second.returncode, 6, second.stdout)
        error = json.loads(second.stderr)
        self.assertEqual(error["ok"], False)
        self.assertEqual(error["error"]["code"], "genesis_error")

        after = TrustedState.load(self.home)
        self.assertEqual(after.snapshot(), before_snapshot)
        self.assertEqual(after.records(), before_records)

    def test_wrong_host_binding_load_fails_authority_check(self) -> None:
        completed = self._run_genesis()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        trusted = TrustedState.load(self.home)
        with self.assertRaises(AuthorityError):
            trusted.set_authority(authority="on", host_binding="different-host")
        self.assertTrue(TrustedState.load(self.home).verify())

    def test_receipt_json_parses_with_expected_keys(self) -> None:
        completed = self._run_genesis()
        value = json.loads(completed.stdout)
        self.assertEqual(set(value), {"ok", "result"})
        self.assertEqual(value["ok"], True)
        result = value["result"]
        for key in ("who", "why", "root", "head", "authority", "evidence_ref", "home"):
            self.assertIn(key, result)
        self.assertTrue(result["who"])
        self.assertTrue(result["why"])
        self.assertTrue(result["root"])
        self.assertTrue(result["head"])
        self.assertTrue(result["authority"])
        self.assertTrue(result["evidence_ref"])
        self.assertTrue(result["home"])


if __name__ == "__main__":
    unittest.main()
