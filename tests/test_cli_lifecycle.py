from __future__ import annotations

import json
from multiprocessing.connection import Client
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agentic_evo.ipc import (
    ServiceUnavailableError,
    SurfaceClient,
    service_endpoint,
)
from agentic_evo.runtime import DevelopmentalRuntime


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class CLILifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "runtime"
        self.host_binding = "test-host-binding"
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding=self.host_binding,
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
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

    def _run_cli(
        self,
        *arguments: str,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                *arguments,
                "--dev-home",
                str(self.home),
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

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

    def _wait_until_ready(
        self,
        process: subprocess.Popen[str],
    ) -> SurfaceClient:
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

    def test_cli_hook_uses_surface_and_status_reports_process_rehearsal(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)

        status_result = self._run_cli("status")
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        status = json.loads(status_result.stdout)
        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["body_rehearsal"]["state"], "ready")

        hook_result = self._run_cli(
            "hook",
            input_text=json.dumps(
                {
                    "session_id": "cli-session",
                    "cwd": "C:/work/project-a",
                    "hook_event_name": "SessionStart",
                    "model": "model-a",
                    "source": "startup",
                }
            ),
        )
        self.assertEqual(hook_result.returncode, 0, hook_result.stderr)
        hook_output = json.loads(hook_result.stdout)
        self.assertIn(
            "Body zero",
            hook_output["hookSpecificOutput"]["additionalContext"],
        )
        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_start")
        self.assertEqual(last.author_kind, "surface_unverified")

    @unittest.skipUnless(sys.platform == "win32", "Windows native contract")
    def test_windows_public_endpoint_rejects_generic_pipe_clients(self) -> None:
        service = self._spawn_service()
        client = self._wait_until_ready(service)
        endpoint = service_endpoint(self.home)
        generic = None
        try:
            with self.assertRaises(OSError) as denied:
                generic = Client(
                    endpoint.address,
                    family=endpoint.family,
                    authkey=None,
                )
            self.assertEqual(denied.exception.winerror, 5)
        finally:
            if generic is not None:
                generic.close()

        self.assertEqual(client.status()["root"], self.runtime.status().root)

    def test_rehearsal_off_survives_service_crash_and_restart(
        self,
    ) -> None:
        first = self._spawn_service()
        client = self._wait_until_ready(first)
        before = client.status()
        client.wake(
            execution_surface="codex",
            session_id="session-before-off",
            project_environment="project-a",
        )

        off_result = self._run_cli("off")
        self.assertEqual(off_result.returncode, 0, off_result.stderr)
        off_payload = json.loads(off_result.stdout)["result"]
        self.assertEqual(off_payload["authority"], "off")
        self.assertEqual(off_payload["body_rehearsal"]["state"], "absent")
        off_record = self.runtime.evidence.records()[-1]
        self.assertEqual(off_record.event_kind, "control_rehearsal_off")
        self.assertEqual(off_record.source_kind, "host_control_rehearsal")
        self.assertEqual(off_record.author_kind, "control_unverified")
        after_off = client.status()
        self.assertEqual(after_off["body_rehearsal"]["state"], "absent")
        self.assertEqual(after_off["active_sessions"], [])
        self.assertEqual(after_off["root"], before["root"])
        self.assertEqual(after_off["head"], before["head"])

        first.kill()
        first.wait(timeout=5)
        replacement = self._spawn_service()
        replacement_client = self._wait_until_ready(replacement)
        persisted = replacement_client.status()
        self.assertEqual(persisted["authority"], "off")
        self.assertEqual(persisted["body_rehearsal"]["state"], "absent")
        self.assertEqual(persisted["root"], before["root"])
        self.assertEqual(persisted["head"], before["head"])

    def test_off_requires_live_control_service_and_never_falls_back_to_sqlite(
        self,
    ) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        self._terminate(service)
        before = self.runtime.status()
        before_records = self.runtime.evidence.records()

        result = self._run_cli("off")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.runtime.status(), before)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_malformed_and_oversize_hook_input_fails_open_without_echo(
        self,
    ) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        before = self.runtime.evidence.records()
        secret = "private-hook-content-that-must-not-be-echoed"

        malformed = self._run_cli("hook", input_text="{not-json-" + secret)
        oversized = self._run_cli(
            "hook",
            input_text=secret + ("x" * (2 * 1024 * 1024)),
        )
        deeply_nested = self._run_cli(
            "hook",
            input_text=("[" * 100_000) + "0" + ("]" * 100_000),
        )

        for result in (malformed, oversized, deeply_nested):
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertNotIn(secret, result.stderr)
        self.assertEqual(self.runtime.evidence.records(), before)


if __name__ == "__main__":
    unittest.main()
