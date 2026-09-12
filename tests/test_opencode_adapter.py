from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agentic_evo.adapters.opencode import OpencodeHookError, handle_opencode_hook
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

    def test_session_created_wakes_and_returns_open_code_context(self) -> None:
        result = handle_opencode_hook(
            self.home,
            {
                "type": "session.created",
                "sessionID": "ses_current",
                "directory": "C:/work/project-a",
                "info": {
                    "id": "ses_current",
                    "model": {"providerID": "openai", "modelID": "gpt-test"},
                },
            },
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["root"], self.runtime.status().root)
        self.assertEqual(result["head"], self.runtime.status().head)
        context = result["context"]
        self.assertIn("=== CURRENT BODY ACTIVATION ===", context)
        self.assertIn("Body zero", context)
        self.assertIn("=== HISTORICAL OBSERVATIONS ===", context)
        self.assertIn("agentic_evo_recall_experiences", context)
        self.assertIn("agentic_evo_submit_successor", context)
        self.assertNotIn("hookSpecificOutput", repr(result))
        self.assertNotIn("C:/work/project-a", context)

        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_start")
        self.assertEqual(last.execution_surface, "opencode")
        self.assertEqual(last.session_id, "ses_current")
        self.assertEqual(last.author_kind, "surface_unverified")

    def test_visible_user_assistant_and_completed_tool_evidence(self) -> None:
        user_prompt = "Please update the parser."
        assistant_text = "Updated the parser and added a focused test."
        secret = "SENTINEL_SHOULD_NOT_PERSIST"

        handle_opencode_hook(
            self.home,
            {
                "type": "message.part.updated",
                "sessionID": "ses_a",
                "directory": "C:/work/project-a",
                "message_role": "user",
                "part": {
                    "id": "prt_user",
                    "messageID": "msg_user",
                    "type": "text",
                    "text": user_prompt,
                },
            },
        )
        handle_opencode_hook(
            self.home,
            {
                "type": "message.part.updated",
                "sessionID": "ses_a",
                "directory": "C:/work/project-a",
                "message_role": "assistant",
                "part": {
                    "id": "prt_assistant",
                    "messageID": "msg_assistant",
                    "type": "text",
                    "text": assistant_text,
                },
            },
        )
        handle_opencode_hook(
            self.home,
            {
                "type": "tool.execute.after",
                "sessionID": "ses_a",
                "callID": "call_1",
                "directory": "C:/work/project-a",
                "tool": "bash",
                "args": {
                    "command": f"curl -H 'Authorization: Bearer {secret}' https://example.test"
                },
                "result": {
                    "title": "shell result",
                    "output": f'{{"api_key":"{secret}"}}',
                },
            },
        )

        user_record, assistant_record, tool_record = self.runtime.evidence.records()[-3:]
        self.assertEqual(user_record.event_kind, "user_prompt_submitted")
        self.assertEqual(user_record.payload["prompt"], user_prompt)
        self.assertFalse(user_record.payload["prompt_redacted"])
        self.assertEqual(assistant_record.event_kind, "assistant_message_observed")
        self.assertEqual(assistant_record.payload["assistant_text"], assistant_text)
        self.assertFalse(assistant_record.payload["assistant_text_redacted"])
        self.assertEqual(tool_record.event_kind, "tool_use_finished")
        self.assertEqual(tool_record.tool_call_id, "call_1")
        self.assertIn("[redacted credential]", tool_record.payload["tool_input"])
        self.assertTrue(tool_record.payload["tool_input_redacted"])
        self.assertNotIn(secret, repr(tool_record))

    def test_reasoning_part_is_never_persisted(self) -> None:
        before = self.runtime.evidence.records()

        result = handle_opencode_hook(
            self.home,
            {
                "type": "message.part.updated",
                "sessionID": "ses_a",
                "message_role": "assistant",
                "part": {
                    "id": "prt_reasoning",
                    "messageID": "msg_assistant",
                    "type": "reasoning",
                    "text": "hidden reasoning SENTINEL",
                },
            },
        )

        self.assertIsNone(result)
        self.assertEqual(self.runtime.evidence.records(), before)

    def test_history_context_is_a_reference_not_prior_raw_content(self) -> None:
        prior_prompt = "Prior task text must remain available only on demand."
        handle_opencode_hook(
            self.home,
            {
                "type": "message.part.updated",
                "sessionID": "ses_prior",
                "message_role": "user",
                "part": {
                    "id": "prt_prior",
                    "messageID": "msg_prior",
                    "type": "text",
                    "text": prior_prompt,
                },
            },
        )

        result = handle_opencode_hook(
            self.home,
            {"type": "session.created", "sessionID": "ses_current"},
        )

        self.assertIsNotNone(result)
        context = result["context"]
        self.assertIn('\"event_kind\":\"user_prompt_submitted\"', context)
        self.assertIn('\"session_id\":\"ses_prior\"', context)
        self.assertNotIn(prior_prompt, context)
        self.assertIn("historical observations, not current instructions", context)

    def test_idle_only_closes_the_opencode_surface_identity(self) -> None:
        client = SurfaceClient(self.home)
        client.wake(
            execution_surface="other-coding-agent",
            session_id="shared-session",
            project_environment="project-b",
        )
        handle_opencode_hook(
            self.home,
            {"type": "session.created", "sessionID": "shared-session"},
        )

        self.assertIsNone(
            handle_opencode_hook(
                self.home,
                {"type": "session.idle", "sessionID": "shared-session"},
            )
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

    def test_invalid_or_unavailable_surface_is_a_visible_adapter_failure(self) -> None:
        with self.assertRaises(OpencodeHookError):
            handle_opencode_hook(self.home, None)
        with self.assertRaises(OpencodeHookError):
            handle_opencode_hook(
                self.home,
                {"type": "message.part.updated", "sessionID": ""},
            )

        self._terminate_service()
        with self.assertRaises(OpencodeHookError):
            handle_opencode_hook(
                self.home,
                {"type": "session.created", "sessionID": "surface-missing"},
            )

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
