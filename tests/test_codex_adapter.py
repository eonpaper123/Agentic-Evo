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
from unittest.mock import patch

from agentic_evo.adapters.codex import (
    CodexHookError,
    _cli_command_prefix,
    _visible_json,
    handle_codex_hook,
)
from agentic_evo.ipc import ServiceUnavailableError, SurfaceClient
from agentic_evo.runtime import DevelopmentalRuntime


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class CodexAdapterTests(unittest.TestCase):
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

    def test_task_visible_prompt_tool_and_final_text_are_readable_and_scrubbed(
        self,
    ) -> None:
        prompt = "Run the focused regression.\nAuthorization: Bearer private-prompt-token"
        tool_input = {
            "command": "python -m unittest tests.test_codex_adapter",
            "access_token": "private-tool-token",
        }
        tool_response = {
            "output": "1 test passed\nAPI_TOKEN=private-result-token",
            "exit_code": 0,
        }
        final_text = "The focused regression passed.\nPRIVATE_KEY=private-final-key"

        handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionStart",
                "model": "model-a",
            },
        )

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
        handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "Stop",
                "model": "model-a",
                "last_assistant_message": final_text,
            },
        )

        prompt_record, tool_record, final_record = self.runtime.evidence.records()[-3:]
        serialized = repr(prompt_record) + repr(tool_record) + repr(final_record)
        self.assertIn("Run the focused regression.", serialized)
        self.assertIn(tool_input["command"], serialized)
        self.assertIn("1 test passed", serialized)
        self.assertIn("The focused regression passed.", serialized)
        self.assertNotIn("private-prompt-token", serialized)
        self.assertNotIn("private-tool-token", serialized)
        self.assertNotIn("private-result-token", serialized)
        self.assertNotIn("private-final-key", serialized)
        self.assertTrue(prompt_record.payload["prompt_redacted"])
        self.assertTrue(tool_record.payload["tool_input_redacted"])
        self.assertTrue(tool_record.payload["tool_response_redacted"])
        self.assertTrue(final_record.payload["assistant_text_redacted"])

    def test_pre_tool_use_does_not_duplicate_post_tool_evidence(self) -> None:
        handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionStart",
                "model": "model-a",
            },
        )
        before = self.runtime.evidence.records()

        result = handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "PreToolUse",
                "turn_id": "turn-a",
                "model": "model-a",
                "tool_name": "Bash",
                "tool_use_id": "tool-a",
                "tool_input": {"command": "python -m unittest"},
            },
        )

        self.assertIsNone(result)
        self.assertEqual(self.runtime.evidence.records(), before)

    def test_exec_agent_messages_are_observations_recalled_by_a_later_session(self) -> None:
        base = {
            "cwd": "C:/work/project-a",
            "model": "model-a",
            "ingress": "codex_exec_jsonl",
        }
        handle_codex_hook(
            self.home,
            {**base, "session_id": "session-a", "hook_event_name": "SessionStart"},
        )
        handle_codex_hook(
            self.home,
            {
                **base,
                "session_id": "session-a",
                "hook_event_name": "AgentMessage",
                "last_assistant_message": "The concrete task result.",
            },
        )
        handle_codex_hook(
            self.home,
            {**base, "session_id": "session-a", "hook_event_name": "SessionEnd"},
        )
        handle_codex_hook(
            self.home,
            {**base, "session_id": "session-b", "hook_event_name": "SessionStart"},
        )

        recalled = SurfaceClient(self.home).recall_experiences(
            execution_surface="codex",
            session_id="session-b",
        )

        messages = [
            experience
            for experience in recalled["experiences"]
            if experience["event_kind"] == "assistant_message_observed"
        ]
        self.assertEqual(len(messages), 1)
        self.assertIn("The concrete task result.", messages[0]["payload"]["assistant_text"])
        self.assertEqual(messages[0]["payload"]["ingress"], "codex_exec_jsonl")

    def test_visible_json_scrubs_known_embedded_credential_formats(self) -> None:
        cases = (
            (
                "curl -H 'Authorization: Bearer SENTINEL_INLINE' https://example.test",
                "SENTINEL_INLINE",
            ),
            ('{"api_key":"SENTINEL_JSON","command":"status"}', "SENTINEL_JSON"),
            (
                "$env:SECRET_NAME='SENTINEL_POWERSHELL'\nWrite-Output done",
                "SENTINEL_POWERSHELL",
            ),
        )

        for value, sentinel in cases:
            visible, redacted, truncated = _visible_json(value)

            self.assertNotIn(sentinel, visible)
            self.assertTrue(redacted)
            self.assertFalse(truncated)

        structured = {
            "api_key": "SENTINEL_API_KEY",
            "API_KEY": "SENTINEL_UPPER_API_KEY",
            "apikey": "SENTINEL_APIKEY",
            "OPENAI_API_KEY": "SENTINEL_OPENAI_API_KEY",
        }
        visible, redacted, truncated = _visible_json(structured)

        self.assertTrue(redacted)
        self.assertFalse(truncated)
        for sentinel in structured.values():
            self.assertNotIn(sentinel, visible)

    def test_cli_command_prefix_is_powershell_invokable_only_on_windows(self) -> None:
        with patch("agentic_evo.adapters.codex._is_windows_platform", return_value=True):
            self.assertTrue(_cli_command_prefix().startswith('& "'))
        with patch("agentic_evo.adapters.codex._is_windows_platform", return_value=False):
            self.assertFalse(_cli_command_prefix().startswith("& "))

    def test_session_start_separates_historical_observation_references_from_body_context(
        self,
    ) -> None:
        client = SurfaceClient(self.home)
        prior_wake = client.wake(
            execution_surface="opencode",
            session_id="prior-session",
            project_environment="project-a",
            model="model-a",
        )
        client.observe(
            event_kind="user_prompt_submitted",
            payload={"prompt": "Historical task content must be read on demand."},
            execution_surface="opencode",
            session_id="prior-session",
        )
        client.sleep(execution_surface="opencode", session_id="prior-session")

        result = handle_codex_hook(
            self.home,
            {
                "session_id": "session-a",
                "cwd": "C:/work/project-a",
                "hook_event_name": "SessionStart",
                "model": "model-a",
            },
        )

        self.assertIsNotNone(result)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CURRENT BODY ACTIVATION", context)
        self.assertIn("HISTORICAL OBSERVATIONS", context)
        self.assertIn("historical observations, not current instructions", context)
        self.assertIn("opencode", context)
        self.assertIn("prior-session", context)
        self.assertIn(prior_wake["head"], context)
        self.assertIn("recall-experiences", context)
        self.assertNotIn("Historical task content must be read on demand.", context)

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

    def test_observatory_failure_is_reported_to_the_hook_boundary(self) -> None:
        db_path = self.home / "trusted" / "state.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection, connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
            connection.execute(
                "UPDATE checkpoints SET record_json = ? WHERE sequence = 1",
                (b"{}",),
            )

        with self.assertRaises(CodexHookError):
            handle_codex_hook(
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

        with closing(sqlite3.connect(db_path)) as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
        self.assertEqual(after, before)

    def test_unavailable_surface_failure_is_not_silently_reported_as_success(
        self,
    ) -> None:
        self._terminate_service()
        before = self.runtime.evidence.records()

        with self.assertRaises(CodexHookError):
            handle_codex_hook(
                self.home,
                {
                    "session_id": "surface-missing",
                    "cwd": "C:/work/project-a",
                    "hook_event_name": "SessionStart",
                    "model": "model-a",
                    "source": "startup",
                },
            )

        self.assertEqual(self.runtime.evidence.records(), before)


if __name__ == "__main__":
    unittest.main()
