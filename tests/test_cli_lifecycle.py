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

    def _spawn_surface_stdio(
        self,
        execution_surface: str,
    ) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                "surface-stdio",
                "--execution-surface",
                execution_surface,
                "--dev-home",
                str(self.home),
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        return process

    def _write_jsonl(
        self,
        process: subprocess.Popen[str],
        value: object,
    ) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()

    def _read_jsonl(self, process: subprocess.Popen[str]) -> dict[str, object]:
        assert process.stdout is not None
        line = process.stdout.readline()
        self.assertTrue(line, "surface-stdio exited before responding")
        value = json.loads(line)
        self.assertIsInstance(value, dict)
        return value

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

    def test_surface_stdio_status_uses_external_process_boundary(self) -> None:
        service = self._spawn_service()
        client = self._wait_until_ready(service)
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "status-1",
                "op": "status",
                "args": {},
            },
        )
        response = self._read_jsonl(process)
        direct = client.status()

        self.assertEqual(response["schema"], "agentic-evo.surface-stdio.v1")
        self.assertEqual(response["id"], "status-1")
        self.assertTrue(response["ok"])
        result = response["result"]
        self.assertIsInstance(result, dict)
        self.assertEqual(result["root"], direct["root"])
        self.assertEqual(result["head"], direct["head"])
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_surface_stdio_wake_and_sleep_bind_fixed_execution_surface(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-1",
                "op": "wake",
                "args": {"session_id": "s1", "project_environment": "proj-a"},
            },
        )
        self.assertTrue(self._read_jsonl(process)["ok"])
        session_start = self.runtime.evidence.records()[-1]
        self.assertEqual(session_start.event_kind, "session_start")
        self.assertEqual(session_start.execution_surface, "generic-stdio")
        self.assertEqual(session_start.session_id, "s1")
        self.assertEqual(session_start.author_kind, "surface_unverified")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "sleep-1",
                "op": "sleep",
                "args": {"session_id": "s1"},
            },
        )
        self.assertTrue(self._read_jsonl(process)["ok"])
        session_end = self.runtime.evidence.records()[-1]
        self.assertEqual(session_end.event_kind, "session_end")
        self.assertEqual(session_end.execution_surface, "generic-stdio")
        self.assertEqual(session_end.session_id, "s1")
        self.assertFalse(
            any(
                session.execution_surface == "generic-stdio"
                for session in self.runtime.status().active_sessions
            )
        )

    def test_surface_stdio_same_raw_session_id_coexists_with_codex_surface(self) -> None:
        service = self._spawn_service()
        client = self._wait_until_ready(service)
        client.wake(
            execution_surface="codex",
            session_id="shared",
            project_environment="proj-a",
        )
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-shared",
                "op": "wake",
                "args": {"session_id": "shared", "project_environment": "proj-a"},
            },
        )
        self.assertTrue(self._read_jsonl(process)["ok"])
        self.assertEqual(
            {
                (session["execution_surface"], session["session_id"])
                for session in client.status()["active_sessions"]
            },
            {("codex", "shared"), ("generic-stdio", "shared")},
        )

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "sleep-shared",
                "op": "sleep",
                "args": {"session_id": "shared"},
            },
        )
        self.assertTrue(self._read_jsonl(process)["ok"])
        self.assertEqual(
            client.status()["active_sessions"],
            [{"execution_surface": "codex", "session_id": "shared"}],
        )

    def test_surface_stdio_observe_allows_omitted_session_id(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "observe-1",
                "op": "observe",
                "args": {"event_kind": "tool_result", "payload": {"outcome": "ok"}},
            },
        )
        self.assertTrue(self._read_jsonl(process)["ok"])
        record = self.runtime.evidence.records()[-1]
        self.assertEqual(record.execution_surface, "generic-stdio")
        self.assertIsNone(record.session_id)

    def test_surface_stdio_invalid_line_returns_invalid_input_without_mutation(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        secret = "surface-stdio-secret-probe"
        process = self._spawn_surface_stdio("generic-stdio")

        assert process.stdin is not None
        process.stdin.write("{not-json-" + secret + "\n")
        process.stdin.flush()
        response = self._read_jsonl(process)

        self.assertFalse(response["ok"])
        error = response["error"]
        self.assertIsInstance(error, dict)
        self.assertEqual(error["code"], "invalid_input")
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)
        assert process.stderr is not None
        self.assertNotIn(secret, process.stderr.read())

    def test_surface_stdio_rejects_argument_mismatch_before_surface_call(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio("generic-stdio")

        for request_id, operation, args in (
            ("sleep-empty", "sleep", {}),
            ("observe-array", "observe", {"event_kind": "tool_result", "payload": []}),
        ):
            self._write_jsonl(
                process,
                {
                    "schema": "agentic-evo.surface-stdio.v1",
                    "id": request_id,
                    "op": operation,
                    "args": args,
                },
            )
            response = self._read_jsonl(process)
            self.assertFalse(response["ok"])
            error = response["error"]
            self.assertIsInstance(error, dict)
            self.assertEqual(error["code"], "invalid_input")

        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_surface_stdio_witness_unavailable_returns_service_unavailable_without_exit(self) -> None:
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "status-unavailable",
                "op": "status",
                "args": {},
            },
        )
        response = self._read_jsonl(process)

        self.assertFalse(response["ok"])
        error = response["error"]
        self.assertIsInstance(error, dict)
        self.assertEqual(error["code"], "service_unavailable")
        self.assertIsNone(process.poll())
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)

    def test_surface_stdio_eof_exits_zero_without_implicit_sleep(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-eof",
                "op": "wake",
                "args": {"session_id": "eof-session", "project_environment": "proj-a"},
            },
        )
        self.assertTrue(self._read_jsonl(process)["ok"])
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertIn(
            ("generic-stdio", "eof-session"),
            {
                (session.execution_surface, session.session_id)
                for session in self.runtime.status().active_sessions
            },
        )

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
