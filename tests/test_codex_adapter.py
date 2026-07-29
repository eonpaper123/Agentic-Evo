from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agentic_evo.adapters.codex import handle_codex_hook
from agentic_evo.runtime import DevelopmentalRuntime


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

    def tearDown(self) -> None:
        self.tempdir.cleanup()

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

    def test_observatory_failure_does_not_block_the_coding_agent_hook(self) -> None:
        log_path = self.home / "evidence" / "events.jsonl"
        record = json.loads(log_path.read_text(encoding="utf-8"))
        record["event_kind"] = "tampered-genesis"
        log_path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
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
        self.assertEqual(len(log_path.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
