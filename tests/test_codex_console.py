from __future__ import annotations

import argparse
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentic_evo.cli import _run_codex_console
from agentic_evo.adapters.experience_projection import MAX_VISIBLE_TEXT_BYTES


class CodexConsoleTests(unittest.TestCase):
    def test_two_tasks_resume_the_exact_managed_thread_and_render_plain_output(self) -> None:
        hook_events: list[str] = []
        stream_calls: list[dict[str, object]] = []

        def handle_hook(_home: Path, payload: dict[str, object], **kwargs):
            event = str(payload["hook_event_name"])
            hook_events.append(event)
            if event == "SessionStart":
                kwargs["on_session_started"]()
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "ACTIVE BODY",
                    }
                }
            return None

        def run_stream(**kwargs):
            stream_calls.append(kwargs)
            for event in (
                {"type": "thread.started", "thread_id": "native-thread"},
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": f"answer-{len(stream_calls)}",
                    },
                },
                {"type": "turn.completed"},
            ):
                kwargs["on_event"](event)
            return SimpleNamespace(
                thread_id="native-thread",
                turn_completed=True,
                exit_code=0,
                protocol_complete=True,
                failure=None,
            )

        arguments = argparse.Namespace(
            home=Path("D:/runtime"),
            codex_executable=Path("D:/apps/codex.exe"),
            cwd=Path.cwd(),
            prompt=None,
            model="gpt-5.6-sol",
            effort="xhigh",
            approve_for_me=True,
            surface="codex",
        )
        output = io.StringIO()
        with (
            patch("agentic_evo.cli.ensure_witness"),
            patch("agentic_evo.cli.handle_codex_hook", side_effect=handle_hook),
            patch("agentic_evo.cli.run_codex_exec_stream", side_effect=run_stream),
            patch("agentic_evo.cli.SurfaceClient"),
            patch("builtins.input", side_effect=["first task", "second task", ":exit"]),
            patch("sys.stdout", output),
        ):
            result = _run_codex_console(arguments)

        self.assertEqual(result, 0)
        self.assertEqual(
            [call["resume_thread_id"] for call in stream_calls],
            [None, "native-thread"],
        )
        self.assertIn("--approve-for-me", stream_calls[0]["args"])
        self.assertNotIn("--approve-for-me", stream_calls[1]["args"])
        self.assertIn("ACTIVE BODY", str(stream_calls[0]["wrapped_prompt"]))
        self.assertEqual(stream_calls[1]["wrapped_prompt"], "second task")
        self.assertTrue(all(call["forward_jsonl"] is False for call in stream_calls))
        self.assertIn("answer-1", output.getvalue())
        self.assertIn("answer-2", output.getvalue())
        self.assertNotIn('"type":"item.completed"', output.getvalue())
        self.assertEqual(hook_events[0], "SessionStart")
        self.assertEqual(hook_events[-1], "SessionEnd")

    def test_tool_output_is_bounded_and_redacted_before_display(self) -> None:
        def handle_hook(_home: Path, payload: dict[str, object], **kwargs):
            if payload["hook_event_name"] == "SessionStart":
                kwargs["on_session_started"]()
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "ACTIVE BODY",
                    }
                }
            return None

        secret = "should-not-be-displayed"

        def run_stream(**kwargs):
            for event in (
                {"type": "thread.started", "thread_id": "native-thread"},
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "inspect",
                        "aggregated_output": (
                            f"API_KEY={secret}\n" + "x" * (MAX_VISIBLE_TEXT_BYTES * 2)
                        ),
                        "exit_code": 0,
                        "status": "completed",
                    },
                },
                {"type": "turn.completed"},
            ):
                kwargs["on_event"](event)
            return SimpleNamespace(
                thread_id="native-thread",
                turn_completed=True,
                exit_code=0,
                protocol_complete=True,
                failure=None,
            )

        arguments = argparse.Namespace(
            home=Path("D:/runtime"),
            codex_executable=Path("D:/apps/codex.exe"),
            cwd=Path.cwd(),
            prompt="inspect",
            model="gpt-5.6-sol",
            effort="xhigh",
            approve_for_me=False,
            surface="codex",
        )
        output = io.StringIO()
        with (
            patch("agentic_evo.cli.ensure_witness"),
            patch("agentic_evo.cli.handle_codex_hook", side_effect=handle_hook),
            patch("agentic_evo.cli.run_codex_exec_stream", side_effect=run_stream),
            patch("agentic_evo.cli.SurfaceClient"),
            patch("builtins.input", return_value=":exit"),
            patch("sys.stdout", output),
        ):
            result = _run_codex_console(arguments)

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertNotIn(secret, rendered)
        self.assertIn("[command output redacted, truncated for display]", rendered)
        self.assertLess(len(rendered), MAX_VISIBLE_TEXT_BYTES + 500)


if __name__ == "__main__":
    unittest.main()
