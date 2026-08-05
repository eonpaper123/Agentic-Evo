from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agentic_evo._util import sha256_hex
from agentic_evo.ipc import ServiceUnavailableError, SurfaceClient
from agentic_evo.runtime import SURFACE_CONTEXT_ACTIVATION_KIND, DevelopmentalRuntime
from agentic_evo.runtime_adopt import adopt_genesis_home, verify_adopted_home
from agentic_evo.trusted import TrustedState


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"

HOST_BINDING = "host-alpha-machine"
PURPOSE_ANCHOR = "Improve the future of the one bound host."
ROOT = "agentic-evo-root-v1"
# Mirror the real born home: a human-pinned 40-hex genesis head that is NOT a
# sha256 body commitment (a git commit SHA), proving adopt handles it.
INITIAL_HEAD = "ee79ae49b8941344e1f314e1f9183deacf8c089d"


class RuntimeAdoptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "identity-home"
        self.processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        for process in reversed(self.processes):
            self._terminate(process)
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

    def _run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                *arguments,
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def _run_genesis(self) -> dict[str, object]:
        completed = self._run_cli(
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
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        value = json.loads(completed.stdout)
        self.assertEqual(value["ok"], True)
        return value["result"]

    def _run_adopt(self) -> dict[str, object]:
        completed = self._run_cli("runtime-adopt", "--home", str(self.home))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        value = json.loads(completed.stdout)
        self.assertEqual(value["ok"], True)
        return value["result"]

    def _run_adopt_failure(self) -> subprocess.CompletedProcess[str]:
        completed = self._run_cli("runtime-adopt", "--home", str(self.home))
        self.assertEqual(completed.returncode, 6, completed.stdout)
        error = json.loads(completed.stderr)
        self.assertEqual(error["ok"], False)
        self.assertEqual(error["error"]["code"], "runtime_adopt_error")
        return completed

    def _spawn_service(self) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                "serve",
                "--dev-home",
                str(self.home),
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        return process

    def _wait_until_ready(self, process: subprocess.Popen[str]) -> SurfaceClient:
        client = SurfaceClient(self.home)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=1)
                self.fail(
                    f"CLI service exited before ready "
                    f"(code={process.returncode}): {stderr}"
                )
            try:
                client.status()
                return client
            except ServiceUnavailableError:
                time.sleep(0.02)
        self.fail("CLI service did not become ready")

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    def test_genesis_then_adopt_preserves_identity_and_genesis_evidence(self) -> None:
        genesis = self._run_genesis()
        self.assertEqual(genesis["root"], ROOT)
        self.assertEqual(genesis["head"], INITIAL_HEAD)
        self.assertEqual(genesis["who"], sha256_hex(HOST_BINDING))
        self.assertEqual(genesis["why"], sha256_hex(PURPOSE_ANCHOR))
        self.assertEqual(genesis["authority"], "on")

        before = TrustedState.load(self.home)
        before_snapshot = before.snapshot()
        before_records = before.records()
        self.assertEqual(len(before_records), 1)
        self.assertEqual(before_records[0].event_kind, "genesis")
        self.assertEqual(before_records[0].head_after, INITIAL_HEAD)

        adopted = self._run_adopt()
        identity = adopted["identity"]
        self.assertEqual(identity["who"], genesis["who"])
        self.assertEqual(identity["why"], genesis["why"])
        self.assertEqual(identity["root"], ROOT)
        self.assertEqual(identity["authority"], "on")
        self.assertEqual(adopted["genesis_head"], INITIAL_HEAD)
        commitment = adopted["head"]
        self.assertIsInstance(commitment, str)
        self.assertEqual(len(commitment), 64)
        self.assertNotEqual(commitment, INITIAL_HEAD)
        initial_body = adopted["initial_body"]
        self.assertEqual(initial_body["commitment"], commitment)
        self.assertEqual(initial_body["generation"], 1)
        self.assertEqual(initial_body["parent_head"], INITIAL_HEAD)
        self.assertEqual(initial_body["activation_kind"], SURFACE_CONTEXT_ACTIVATION_KIND)
        self.assertEqual(initial_body["activation_artifact"], "entrypoint.md")
        self.assertEqual(adopted["evidence"]["records"], 2)
        self.assertEqual(adopted["evidence"]["genesis"]["sequence"], 1)
        self.assertEqual(
            adopted["evidence"]["genesis"]["integrity_hash"],
            genesis["evidence_ref"]["integrity_hash"],
        )
        self.assertEqual(adopted["evidence"]["adoption"]["sequence"], 2)
        self.assertEqual(adopted["evidence"]["adoption"]["event_kind"], "head_advanced")

        # Layout: trusted state relocated, originals gone, Body store present.
        self.assertTrue((self.home / "trusted" / "state.sqlite3").is_file())
        self.assertTrue((self.home / "trusted" / "witness.key").is_file())
        self.assertFalse((self.home / "state.sqlite3").exists())
        self.assertFalse((self.home / "witness.key").exists())
        self.assertTrue((self.home / "body" / "manifests" / f"{commitment}.json").is_file())

        # Trusted identity: who/why/root byte-identical; head is the initial Body.
        after = TrustedState.load(self.home / "trusted")
        after_snapshot = after.snapshot()
        self.assertEqual(after_snapshot.who, before_snapshot.who)
        self.assertEqual(after_snapshot.why, before_snapshot.why)
        self.assertEqual(after_snapshot.root, before_snapshot.root)
        self.assertEqual(after_snapshot.head, commitment)
        self.assertEqual(after_snapshot.authority, "on")
        after_records = after.records()
        self.assertEqual(len(after_records), 2)
        self.assertEqual(after_records[0], before_records[0])
        self.assertEqual(after_records[1].event_kind, "head_advanced")
        self.assertEqual(after_records[1].head_before, INITIAL_HEAD)
        self.assertEqual(after_records[1].head_after, commitment)

        # Runtime servable: loads exactly like serve and validates the Body.
        runtime = DevelopmentalRuntime(self.home)
        status = runtime.status()
        self.assertEqual(status.root, ROOT)
        self.assertEqual(status.head, commitment)
        self.assertEqual(status.authority, "on")
        self.assertEqual(status.generation, 1)
        manifest = runtime.body_store.read_manifest(status.head)
        self.assertEqual(manifest.root, ROOT)
        self.assertEqual(manifest.parent_head, INITIAL_HEAD)
        self.assertEqual(manifest.activation_kind, SURFACE_CONTEXT_ACTIVATION_KIND)
        self.assertEqual(manifest.activation_artifact, "entrypoint.md")
        self.assertEqual(set(manifest.file_names), {"entrypoint.md", "identity.md"})
        self.assertTrue(runtime.evidence.verify())

    def test_adopted_home_serves_and_status_smoke(self) -> None:
        genesis = self._run_genesis()
        adopted = self._run_adopt()
        commitment = adopted["head"]

        service = self._spawn_service()
        client = self._wait_until_ready(service)
        projected = client.status()
        self.assertEqual(projected["root"], ROOT)
        self.assertEqual(projected["head"], commitment)
        self.assertEqual(projected["authority"], "on")
        self.assertEqual(projected["lifecycle_state"], "waiting")
        self.assertEqual(projected["body_rehearsal"]["state"], "ready")

        status_cli = self._run_cli("status", "--dev-home", str(self.home))
        self.assertEqual(status_cli.returncode, 0, status_cli.stderr)
        payload = json.loads(status_cli.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["result"]["head"], commitment)
        self.assertEqual(payload["result"]["root"], ROOT)
        self.assertEqual(payload["result"]["authority"], "on")

        self._terminate(service)
        after = TrustedState.load(self.home / "trusted")
        self.assertEqual(after.snapshot().head, commitment)
        self.assertEqual(len(after.records()), 2)
        self.assertEqual(after.records()[0].event_kind, "genesis")

    def test_adopt_refuses_an_already_runtime_home(self) -> None:
        DevelopmentalRuntime.genesis(
            self.home,
            host_binding=HOST_BINDING,
            purpose_anchor=PURPOSE_ANCHOR,
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        self._run_adopt_failure()

    def test_adopt_refuses_a_non_genesis_home(self) -> None:
        self.home.mkdir(parents=True)
        self._run_adopt_failure()

    def test_second_adopt_is_refused(self) -> None:
        self._run_genesis()
        self._run_adopt()
        self._run_adopt_failure()

    def test_adopt_refuses_home_with_existing_body_store(self) -> None:
        self._run_genesis()
        (self.home / "body").mkdir()
        self._run_adopt_failure()

    def test_verify_adopted_home_api_reads_servable_state(self) -> None:
        self._run_genesis()
        adopted = adopt_genesis_home(self.home)
        self.assertEqual(adopted["evidence"]["records"], 2)
        verified = verify_adopted_home(self.home)
        self.assertEqual(verified["servable"], True)
        self.assertEqual(verified["root"], ROOT)
        self.assertEqual(verified["head"], adopted["head"])
        self.assertEqual(verified["evidence_records"], 2)
        self.assertEqual(verified["genesis_sequence"], 1)
        self.assertEqual(verified["genesis_event_kind"], "genesis")


if __name__ == "__main__":
    unittest.main()
