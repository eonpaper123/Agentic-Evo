from __future__ import annotations

import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agentic_evo.codex_exec_stream import CodexExecStreamError, run_codex_exec_stream


class _FakeProcess:
    def __init__(self, stdout: str, *, exit_code: int = 0) -> None:
        self.stdin = _RecordingStdin()
        self.stdout = io.StringIO(stdout)
        self.returncode: int | None = None
        self._exit_code = exit_code
        self.terminated = False

    def wait(self) -> int:
        self.returncode = self._exit_code
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15


class _RecordingStdin(io.StringIO):
    def close(self) -> None:
        self.flushed = True


class CodexExecStreamTests(unittest.TestCase):
    def test_streams_jsonl_and_projects_only_observable_completed_items(self) -> None:
        raw_stdout = "".join(
            (
                '{"type":"thread.started","thread_id":"thread-real"}\n',
                '{"type":"turn.started"}\n',
                (
                    '{"type":"item.completed","item":{"id":"cmd-1",'
                    '"type":"command_execution","command":"pytest",'
                    '"aggregated_output":"1 passed","exit_code":0,'
                    '"status":"completed"}}\n'
                ),
                (
                    '{"type":"item.completed","item":{"id":"reason-1",'
                    '"type":"reasoning","encrypted_content":"private"}}\n'
                ),
                (
                    '{"type":"item.completed","item":{"id":"files-1",'
                    '"type":"file_change","changes":{"src/a.py":"modified"},'
                    '"status":"completed"}}\n'
                ),
                (
                    '{"type":"item.completed","item":{"id":"answer-1",'
                    '"type":"agent_message","text":"Task complete"}}\n'
                ),
                '{"type":"turn.completed","usage":{"input_tokens":123}}\n',
            )
        )
        process = _FakeProcess(raw_stdout)
        projected: list[dict[str, object]] = []
        streamed = io.StringIO()

        with patch("agentic_evo.codex_exec_stream.subprocess.Popen", return_value=process) as popen:
            result = run_codex_exec_stream(
                executable=Path("D:/sample-user/Apps/CodexCLI/codex.exe"),
                args=("--full-auto",),
                cwd=Path("D:/work/project"),
                wrapped_prompt="=== USER TASK ===\nDo the task.",
                on_event=projected.append,
                stdout=streamed,
            )

        self.assertEqual(
            popen.call_args.args[0],
            [
                str(Path("D:/sample-user/Apps/CodexCLI/codex.exe")),
                "exec",
                "--json",
                "--full-auto",
                "-",
            ],
        )
        self.assertEqual(process.stdin.getvalue(), "=== USER TASK ===\nDo the task.")
        self.assertEqual(popen.call_args.kwargs["cwd"], str(Path("D:/work/project")))
        self.assertEqual(streamed.getvalue(), raw_stdout)
        self.assertEqual(
            projected,
            [
                {"type": "thread.started", "thread_id": "thread-real"},
                {
                    "type": "item.completed",
                    "item": {
                        "id": "cmd-1",
                        "type": "command_execution",
                        "command": "pytest",
                        "aggregated_output": "1 passed",
                        "exit_code": 0,
                        "status": "completed",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "files-1",
                        "type": "file_change",
                        "changes": {"src/a.py": "modified"},
                        "status": "completed",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "answer-1",
                        "type": "agent_message",
                        "text": "Task complete",
                    },
                },
                {"type": "turn.completed"},
            ],
        )
        self.assertEqual(result.thread_id, "thread-real")
        self.assertTrue(result.turn_completed)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.protocol_complete)
        self.assertIsNone(result.failure)

    def test_rejects_malformed_external_jsonl_after_streaming_the_line(self) -> None:
        raw_stdout = '{"type":"thread.started","thread_id":"thread-real"}\nnot-json\n'
        process = _FakeProcess(raw_stdout)
        streamed = io.StringIO()

        with (
            patch("agentic_evo.codex_exec_stream.subprocess.Popen", return_value=process),
            self.assertRaisesRegex(CodexExecStreamError, "malformed JSONL"),
        ):
            run_codex_exec_stream(
                executable="codex",
                args=(),
                cwd=Path("D:/work/project"),
                wrapped_prompt="task",
                stdout=streamed,
            )

        self.assertEqual(
            streamed.getvalue(),
            '{"type":"thread.started","thread_id":"thread-real"}\n',
        )
        self.assertTrue(process.terminated)

    def test_stream_transport_is_ascii_safe_for_chinese_output(self) -> None:
        process = _FakeProcess(
            '{"type":"thread.started","thread_id":"thread-real"}\n'
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"任务完成🙂"}}\n'
            '{"type":"turn.completed"}\n'
        )
        streamed = io.StringIO()

        with patch("agentic_evo.codex_exec_stream.subprocess.Popen", return_value=process):
            run_codex_exec_stream(
                executable="codex",
                args=(),
                cwd=Path("D:/work/project"),
                wrapped_prompt="中文任务",
                stdout=streamed,
            )

        streamed.getvalue().encode("ascii")
        decoded = [json.loads(line) for line in streamed.getvalue().splitlines()]
        self.assertEqual(decoded[1]["item"]["text"], "任务完成🙂")

    def test_zero_exit_without_complete_native_lifecycle_is_not_protocol_complete(self) -> None:
        process = _FakeProcess('{"type":"thread.started","thread_id":"thread-real"}\n')

        with patch("agentic_evo.codex_exec_stream.subprocess.Popen", return_value=process):
            result = run_codex_exec_stream(
                executable="codex",
                args=(),
                cwd=Path("D:/work/project"),
                wrapped_prompt="task",
                stdout=io.StringIO(),
            )

        self.assertFalse(result.protocol_complete)
        self.assertFalse(result.turn_completed)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.failure, "Codex exec did not emit turn.completed")

    def test_forwards_an_optional_private_stderr_sink(self) -> None:
        process = _FakeProcess(
            '{"type":"thread.started","thread_id":"thread-real"}\n'
            '{"type":"turn.completed"}\n'
        )
        diagnostic = io.StringIO()

        with patch(
            "agentic_evo.codex_exec_stream.subprocess.Popen",
            return_value=process,
        ) as popen:
            run_codex_exec_stream(
                executable="codex",
                args=(),
                cwd=Path("D:/work/project"),
                wrapped_prompt="task",
                stdout=io.StringIO(),
                stderr=diagnostic,
            )

        self.assertIs(popen.call_args.kwargs["stderr"], diagnostic)

    def test_resumes_exact_thread_without_forwarding_protocol_json(self) -> None:
        process = _FakeProcess(
            '{"type":"thread.started","thread_id":"thread-real"}\n'
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"continued"}}\n'
            '{"type":"turn.completed"}\n'
        )
        streamed = io.StringIO()
        projected: list[dict[str, object]] = []

        with patch(
            "agentic_evo.codex_exec_stream.subprocess.Popen",
            return_value=process,
        ) as popen:
            result = run_codex_exec_stream(
                executable="codex",
                args=("--model", "gpt-5.6-sol"),
                cwd=Path("D:/work/project"),
                wrapped_prompt="continue the task",
                on_event=projected.append,
                stdout=streamed,
                resume_thread_id="thread-real",
                forward_jsonl=False,
            )

        self.assertEqual(
            popen.call_args.args[0],
            [
                "codex",
                "exec",
                "resume",
                "--json",
                "--model",
                "gpt-5.6-sol",
                "thread-real",
                "-",
            ],
        )
        self.assertEqual(streamed.getvalue(), "")
        self.assertEqual(result.thread_id, "thread-real")
        self.assertTrue(result.protocol_complete)
        self.assertEqual(projected[-1], {"type": "turn.completed"})


if __name__ == "__main__":
    unittest.main()
