from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentic_evo.cli import _run_codex


class RunCodexTests(unittest.TestCase):
    def test_wraps_original_task_and_records_the_observable_lifecycle(self) -> None:
        hook_payloads: list[dict[str, object]] = []

        def handle_hook(_home: Path, payload: dict[str, object], **kwargs):
            hook_payloads.append(payload)
            if payload["hook_event_name"] == "SessionStart":
                kwargs["on_session_started"]()
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "CURRENT BODY AND HISTORY REFERENCES",
                    }
                }
            return None

        def run_stream(**kwargs):
            self.assertIn("CURRENT BODY AND HISTORY REFERENCES", kwargs["wrapped_prompt"])
            self.assertIn("修复解析器，并保留原始任务。", kwargs["wrapped_prompt"])
            for event in (
                {"type": "thread.started", "thread_id": "native-thread"},
                {
                    "type": "item.completed",
                    "item": {
                        "id": "cmd-1",
                        "type": "command_execution",
                        "command": "python -m unittest",
                        "aggregated_output": "OK",
                        "exit_code": 0,
                        "status": "completed",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "answer-0",
                        "type": "agent_message",
                        "text": "处理中。",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "answer-1",
                        "type": "agent_message",
                        "text": "已完成。",
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
            dev_home=Path("D:/runtime"),
            codex_executable=Path("D:/apps/codex.exe"),
            cwd=Path.cwd(),
            prompt="修复解析器，并保留原始任务。",
            model="gpt-5.6-sol",
            effort="xhigh",
            approve_for_me=True,
        )
        with (
            patch("agentic_evo.cli.ensure_witness"),
            patch("agentic_evo.cli.handle_codex_hook", side_effect=handle_hook),
            patch("agentic_evo.cli.run_codex_exec_stream", side_effect=run_stream),
            patch("agentic_evo.cli.SurfaceClient") as surface_client,
        ):
            result = _run_codex(arguments)

        self.assertEqual(result, 0)
        self.assertEqual(
            [payload["hook_event_name"] for payload in hook_payloads],
            [
                "SessionStart",
                "UserPromptSubmit",
                "PostToolUse",
                "AgentMessage",
                "AgentMessage",
                "SessionEnd",
            ],
        )
        self.assertEqual(hook_payloads[1]["prompt"], arguments.prompt)
        self.assertEqual(hook_payloads[2]["tool_response"]["exit_code"], 0)
        self.assertEqual(hook_payloads[3]["last_assistant_message"], "处理中。")
        self.assertEqual(hook_payloads[4]["last_assistant_message"], "已完成。")
        observed_calls = surface_client.return_value.observe.call_args_list
        self.assertEqual(
            observed_calls[0].kwargs["payload"],
            {"ingress": "codex_exec_jsonl"},
        )
        self.assertEqual(
            observed_calls[1].kwargs["payload"],
            {"ingress": "codex_exec_jsonl", "codex_thread_id": "native-thread"},
        )
        self.assertEqual(observed_calls[2].kwargs["event_kind"], "codex_turn_completed")
        self.assertNotEqual(observed_calls[1].kwargs["session_id"], "native-thread")

    def test_sleeps_the_agentic_session_when_codex_stream_fails(self) -> None:
        events: list[str] = []

        def handle_hook(_home: Path, payload: dict[str, object], **kwargs):
            event = str(payload["hook_event_name"])
            events.append(event)
            if event == "SessionStart":
                kwargs["on_session_started"]()
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "body",
                    }
                }
            return None

        arguments = argparse.Namespace(
            dev_home=Path("D:/runtime"),
            codex_executable=Path("D:/apps/codex.exe"),
            cwd=Path.cwd(),
            prompt="task",
            model=None,
            effort=None,
            approve_for_me=False,
        )
        with (
            patch("agentic_evo.cli.ensure_witness"),
            patch("agentic_evo.cli.handle_codex_hook", side_effect=handle_hook),
            patch(
                "agentic_evo.cli.run_codex_exec_stream",
                side_effect=OSError("codex unavailable"),
            ),
            patch("agentic_evo.cli._write_json"),
        ):
            result = _run_codex(arguments)

        self.assertNotEqual(result, 0)
        self.assertEqual(events, ["SessionStart", "UserPromptSubmit", "SessionEnd"])

    def test_sleep_failure_makes_a_completed_codex_run_nonzero(self) -> None:
        def handle_hook(_home: Path, payload: dict[str, object], **kwargs):
            if payload["hook_event_name"] == "SessionStart":
                kwargs["on_session_started"]()
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "body",
                    }
                }
            if payload["hook_event_name"] == "SessionEnd":
                raise OSError("sleep unavailable")
            return None

        arguments = argparse.Namespace(
            dev_home=Path("D:/runtime"),
            codex_executable=Path("D:/apps/codex.exe"),
            cwd=Path.cwd(),
            prompt="task",
            model=None,
            effort=None,
            approve_for_me=False,
        )
        completed = SimpleNamespace(
            thread_id="native-thread",
            turn_completed=True,
            exit_code=0,
            protocol_complete=True,
            failure=None,
        )
        with (
            patch("agentic_evo.cli.ensure_witness"),
            patch("agentic_evo.cli.handle_codex_hook", side_effect=handle_hook),
            patch("agentic_evo.cli.run_codex_exec_stream", return_value=completed),
            patch("agentic_evo.cli._write_json"),
        ):
            result = _run_codex(arguments)

        self.assertNotEqual(result, 0)

    def test_context_failure_after_wake_still_sleeps_the_session(self) -> None:
        events: list[str] = []

        def handle_hook(_home: Path, payload: dict[str, object], **kwargs):
            event = str(payload["hook_event_name"])
            events.append(event)
            if event == "SessionStart":
                kwargs["on_session_started"]()
                raise OSError("recall unavailable")
            return None

        arguments = argparse.Namespace(
            dev_home=Path("D:/runtime"),
            codex_executable=Path("D:/apps/codex.exe"),
            cwd=Path.cwd(),
            prompt="task",
            model=None,
            effort=None,
            approve_for_me=False,
        )
        with (
            patch("agentic_evo.cli.ensure_witness"),
            patch("agentic_evo.cli.handle_codex_hook", side_effect=handle_hook),
            patch("agentic_evo.cli._write_json"),
        ):
            result = _run_codex(arguments)

        self.assertNotEqual(result, 0)
        self.assertEqual(events, ["SessionStart", "SessionEnd"])


if __name__ == "__main__":
    unittest.main()
