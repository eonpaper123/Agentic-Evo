from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from agentic_evo.adapters.opencode import handle_opencode_hook
from agentic_evo.ipc import ServiceUnavailableError, SurfaceClient
from agentic_evo.runtime import DevelopmentalRuntime


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    prior = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(SOURCE_ROOT)
        if not prior
        else os.pathsep.join((str(SOURCE_ROOT), prior))
    )
    return environment


class OpencodeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding="test-host-binding",
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        self.service = subprocess.Popen(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.service",
                "--dev-home",
                str(self.home),
            ],
            cwd=REPOSITORY_ROOT,
            env=_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_service)
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        client = SurfaceClient(self.home)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.service.poll() is not None:
                _, stderr = self.service.communicate(timeout=1)
                self.fail(
                    "Witness service exited before adapter tests became ready "
                    f"(code={self.service.returncode}): {stderr}"
                )
            try:
                client.status()
                return
            except ServiceUnavailableError:
                time.sleep(0.02)
        self.fail("Witness service did not become ready for adapter tests")

    def _terminate_service(self) -> None:
        if self.service.poll() is None:
            self.service.terminate()
            try:
                self.service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.service.kill()
                self.service.wait(timeout=5)
        if self.service.stdout is not None:
            self.service.stdout.close()
        if self.service.stderr is not None:
            self.service.stderr.close()

    def test_session_start_wakes_same_body_and_returns_bounded_context(self) -> None:
        result = handle_opencode_hook(
            self.home,
            {
                "session_id": "session-a",
                "transcript_path": "C:/unstable/transcript.jsonl",
                "cwd": "C:/work/project-a",
                "hook_event_name": "session.start",
                "model": "model-a",
                "source": "startup",
            },
        )

        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "session.start")
        context = output["additionalContext"]
        self.assertIn(self.runtime.status().root, context)
        self.assertIn(self.runtime.status().head, context)
        self.assertIn("Body zero", context)
        self.assertNotIn("transcript.jsonl", context)

        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_start")
        self.assertEqual(last.execution_surface, "opencode")
        self.assertEqual(last.session_id, "session-a")
        self.assertEqual(last.author_kind, "surface_unverified")

    def test_confirmed_session_created_event_wakes(self) -> None:
        result = handle_opencode_hook(
            self.home,
            {
                "type": "session.created",
                "sessionID": "ses_confirmed_1",
                "info": {
                    "id": "ses_confirmed_1",
                    "title": "Fix the build",
                    "directory": "C:/work/project-b",
                    "model": {"id": "anthropic/claude-sonnet-4", "providerID": "anthropic"},
                },
            },
        )

        self.assertIsNotNone(result)
        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_start")
        self.assertEqual(last.execution_surface, "opencode")
        self.assertEqual(last.session_id, "ses_confirmed_1")
        self.assertTrue(
            last.project_environment.startswith("sha256:"),
            last.project_environment,
        )

    def test_message_and_tool_payloads_store_hashes_not_raw_content(self) -> None:
        private_prompt = "private task text that must not be copied"
        tool_input = {"command": "build --with-sensitive-arguments"}
        tool_response = {"output": "private build output"}

        handle_opencode_hook(
            self.home,
            {
                "type": "message.updated",
                "sessionID": "session-a",
                "info": {
                    "id": "msg_1",
                    "role": "user",
                    "parts": [{"type": "text", "text": private_prompt}],
                },
            },
        )
        handle_opencode_hook(
            self.home,
            {
                "type": "tool.execute.before",
                "sessionID": "session-a",
                "tool": "bash",
                "callID": "call_1",
                "args": tool_input,
            },
        )
        handle_opencode_hook(
            self.home,
            {
                "type": "tool.execute.after",
                "sessionID": "session-a",
                "tool": "bash",
                "callID": "call_1",
                "args": tool_input,
                "result": tool_response,
            },
        )

        message_record, before_record, after_record = self.runtime.evidence.records()[-3:]
        serialized = (
            repr(message_record) + repr(before_record) + repr(after_record)
        )
        self.assertNotIn(private_prompt, serialized)
        self.assertNotIn(tool_input["command"], serialized)
        self.assertNotIn(tool_response["output"], serialized)
        self.assertEqual(message_record.event_kind, "message_updated")
        self.assertEqual(message_record.payload["message_role"], "user")
        self.assertRegex(message_record.payload["message_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(before_record.event_kind, "tool_use_started")
        self.assertEqual(before_record.tool_call_id, "call_1")
        self.assertRegex(before_record.payload["tool_input_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(after_record.event_kind, "tool_use_finished")
        self.assertRegex(after_record.payload["tool_response_sha256"], r"^[0-9a-f]{64}$")

    def test_session_end_enters_waiting_without_using_transcript_as_protocol(self) -> None:
        handle_opencode_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "session.start",
                "model": "model-a",
                "source": "startup",
            },
        )
        result = handle_opencode_hook(
            self.home,
            {
                "session_id": "session-a",
                "transcript_path": "C:/unstable/transcript.jsonl",
                "cwd": "C:/work/project-a",
                "hook_event_name": "session.end",
                "model": "model-a",
                "reason": "other",
            },
        )

        self.assertIsNone(result)
        self.assertEqual(self.runtime.status().lifecycle_state, "waiting")
        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_end")
        self.assertEqual(last.execution_surface, "opencode")
        self.assertNotIn("transcript_path", last.payload)

    def test_confirmed_session_idle_and_status_idle_sleep(self) -> None:
        handle_opencode_hook(
            self.home,
            {"type": "session.created", "sessionID": "session-a"},
        )
        self.assertIsNone(
            handle_opencode_hook(
                self.home,
                {"type": "session.idle", "sessionID": "session-a"},
            )
        )
        self.assertEqual(self.runtime.status().lifecycle_state, "waiting")

        handle_opencode_hook(
            self.home,
            {"type": "session.created", "sessionID": "session-b"},
        )
        self.assertIsNone(
            handle_opencode_hook(
                self.home,
                {"type": "session.status", "sessionID": "session-b", "status": {"type": "idle"}},
            )
        )
        self.assertEqual(self.runtime.status().lifecycle_state, "waiting")

    def test_session_end_only_closes_the_opencode_surface_identity(self) -> None:
        client = SurfaceClient(self.home)
        client.wake(
            execution_surface="other-coding-agent",
            session_id="shared-session",
            project_environment="project-b",
        )
        handle_opencode_hook(
            self.home,
            {
                "session_id": "shared-session",
                "cwd": "C:/work/project-a",
                "hook_event_name": "session.start",
                "model": "model-a",
                "source": "startup",
            },
        )

        handle_opencode_hook(
            self.home,
            {
                "session_id": "shared-session",
                "cwd": "C:/work/project-a",
                "hook_event_name": "session.end",
                "model": "model-a",
                "reason": "other",
            },
        )

        self.assertEqual(
            client.status()["active_sessions"],
            [
                {
                    "execution_surface": "other-coding-agent",
                    "session_id": "shared-session",
                }
            ],
        )

    def test_unknown_event_mapped_to_execution_surface_event(self) -> None:
        result = handle_opencode_hook(
            self.home,
            {
                "type": "session.heartbeat",
                "sessionID": "session-a",
                "payload": {"unmapped": True},
            },
        )

        self.assertIsNone(result)
        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "execution_surface_event")
        self.assertEqual(last.payload["hook_event"], "session.heartbeat")
        self.assertEqual(last.payload["unmapped_event_name"], "session.heartbeat")

    def test_session_error_observed_with_bounded_fields(self) -> None:
        handle_opencode_hook(
            self.home,
            {
                "type": "session.error",
                "sessionID": "session-a",
                "error": {
                    "name": "ProviderAuthError",
                    "data": {"message": "authentication failed for provider x"},
                },
            },
        )

        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_error")
        self.assertEqual(last.payload["error_name"], "ProviderAuthError")
        self.assertRegex(
            last.payload["error_message_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertNotIn("authentication failed", repr(last))

    def test_malformed_payloads_are_swallowed(self) -> None:
        self.assertIsNone(handle_opencode_hook(self.home, None))
        self.assertIsNone(handle_opencode_hook(self.home, "not-a-mapping"))
        self.assertIsNone(handle_opencode_hook(self.home, [1, 2, 3]))
        before = self.runtime.evidence.records()
        self.assertIsNone(
            handle_opencode_hook(
                self.home,
                {"type": "session.created", "sessionID": None},
            )
        )
        self.assertEqual(self.runtime.evidence.records(), before)

    def test_observatory_failure_does_not_block_the_coding_agent_hook(self) -> None:
        db_path = self.home / "trusted" / "state.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection, connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
            connection.execute(
                "UPDATE checkpoints SET record_json = ? WHERE sequence = 1",
                (b"{}",),
            )

        result = handle_opencode_hook(
            self.home,
            {
                "type": "message.updated",
                "sessionID": "session-a",
                "info": {"id": "msg_1", "role": "user"},
            },
        )

        self.assertIsNone(result)
        with closing(sqlite3.connect(db_path)) as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
        self.assertEqual(after, before)

    def test_unavailable_surface_fails_open_without_direct_database_fallback(
        self,
    ) -> None:
        self._terminate_service()
        before = self.runtime.evidence.records()

        result = handle_opencode_hook(
            self.home,
            {
                "type": "session.created",
                "sessionID": "surface-missing",
            },
        )

        self.assertIsNone(result)
        self.assertEqual(self.runtime.evidence.records(), before)

    def test_caller_owned_import_boundary(self) -> None:
        script = (
            "import sys\n"
            "import agentic_evo.adapters.opencode\n"
            "print('cli_imported=' + str('agentic_evo.cli' in sys.modules))\n"
        )
        completed = subprocess.run(
            [sys.executable, "-P", "-c", script],
            cwd=REPOSITORY_ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("cli_imported=False", completed.stdout)


if __name__ == "__main__":
    unittest.main()
