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

from agentic_evo.adapters.codex import handle_codex_hook
from agentic_evo.ipc import ServiceUnavailableError, SurfaceClient
from agentic_evo.runtime import DevelopmentalRuntime


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class CodexAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding="test-host-binding",
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        environment = os.environ.copy()
        prior = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(SOURCE_ROOT)
            if not prior
            else os.pathsep.join((str(SOURCE_ROOT), prior))
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
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_until_ready()

    def tearDown(self) -> None:
        self._terminate_service()
        self.tempdir.cleanup()

    def _wait_until_ready(self) -> None:
        client = SurfaceClient(self.home)
        deadline = time.monotonic() + 5
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
        result = handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "transcript_path": "C:/unstable/transcript.jsonl",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionStart",
                "model": "model-a",
                "source": "startup",
            },
        )

        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        context = output["additionalContext"]
        self.assertIn(self.runtime.status().root, context)
        self.assertIn(self.runtime.status().head, context)
        self.assertIn("Body zero", context)
        self.assertNotIn("transcript.jsonl", context)

        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_start")
        self.assertEqual(last.execution_surface, "codex")
        self.assertEqual(last.session_id, "session-a")
        self.assertEqual(last.author_kind, "surface_unverified")

    def test_prompt_and_tool_payloads_store_hashes_not_raw_content(self) -> None:
        prompt = "private task text that must not be copied"
        tool_input = {"command": "build --with-sensitive-arguments"}
        tool_response = {"output": "private build output"}

        handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "UserPromptSubmit",
                "turn_id": "turn-a",
                "model": "model-a",
                "prompt": prompt,
            },
        )
        handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "PostToolUse",
                "turn_id": "turn-a",
                "model": "model-a",
                "tool_name": "Bash",
                "tool_use_id": "tool-a",
                "tool_input": tool_input,
                "tool_response": tool_response,
            },
        )

        prompt_record, tool_record = self.runtime.evidence.records()[-2:]
        serialized = repr(prompt_record) + repr(tool_record)
        self.assertNotIn(prompt, serialized)
        self.assertNotIn(tool_input["command"], serialized)
        self.assertNotIn(tool_response["output"], serialized)
        self.assertEqual(prompt_record.payload["prompt_chars"], len(prompt))
        self.assertRegex(prompt_record.payload["prompt_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(tool_record.payload["tool_input_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(tool_record.payload["tool_response_sha256"], r"^[0-9a-f]{64}$")

    def test_session_end_enters_waiting_without_using_transcript_as_protocol(self) -> None:
        handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionStart",
                "model": "model-a",
                "source": "startup",
            },
        )
        result = handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "transcript_path": "C:/unstable/transcript.jsonl",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionEnd",
                "model": "model-a",
                "reason": "other",
            },
        )

        self.assertIsNone(result)
        self.assertEqual(self.runtime.status().lifecycle_state, "waiting")
        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_end")
        self.assertNotIn("transcript_path", last.payload)

    def test_session_end_only_closes_the_codex_surface_identity(self) -> None:
        client = SurfaceClient(self.home)
        client.wake(
            execution_surface="other-coding-agent",
            session_id="shared-session",
            project_environment="project-b",
        )
        handle_codex_hook(
            self.home,
            {
                "session_id": "shared-session",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionStart",
                "model": "model-a",
                "source": "startup",
            },
        )

        handle_codex_hook(
            self.home,
            {
                "session_id": "shared-session",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionEnd",
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

        result = handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "UserPromptSubmit",
                "turn_id": "turn-a",
                "model": "model-a",
                "prompt": "continue the real task",
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

        result = handle_codex_hook(
            self.home,
            {
                "session_id": "surface-missing",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionStart",
                "model": "model-a",
                "source": "startup",
            },
        )

        self.assertIsNone(result)
        self.assertEqual(self.runtime.evidence.records(), before)


if __name__ == "__main__":
    unittest.main()
