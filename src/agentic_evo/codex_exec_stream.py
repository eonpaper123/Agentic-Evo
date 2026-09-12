from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence, TextIO


_OBSERVABLE_COMPLETED_ITEM_FIELDS: dict[str, tuple[str, ...]] = {
    "command_execution": (
        "id",
        "type",
        "command",
        "aggregated_output",
        "exit_code",
        "status",
    ),
    "file_change": ("id", "type", "changes", "status"),
    "agent_message": ("id", "type", "text"),
}


class CodexExecStreamError(RuntimeError):
    """The external Codex JSONL stream did not match the required contract."""


@dataclass(frozen=True)
class CodexExecStreamResult:
    thread_id: str | None
    turn_completed: bool
    exit_code: int
    protocol_complete: bool
    failure: str | None


def run_codex_exec_stream(
    *,
    executable: Path | str,
    args: Sequence[str],
    cwd: Path | str,
    wrapped_prompt: str,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    resume_thread_id: str | None = None,
    forward_jsonl: bool = True,
) -> CodexExecStreamResult:
    """Run ``codex exec --json`` and project only task-visible completion data.

    ``wrapped_prompt`` is sent through stdin using Codex's ``-`` prompt marker,
    keeping the Agent Body and user task out of the process command line.
    Original JSONL remains streamed to ``stdout``; callbacks never receive usage,
    reasoning, encrypted content, or unsupported item types.
    """

    if resume_thread_id is None:
        command = [str(executable), "exec", "--json", *args, "-"]
    else:
        command = [
            str(executable),
            "exec",
            "resume",
            "--json",
            *args,
            resume_thread_id,
            "-",
        ]
    output = sys.stdout if stdout is None else stdout
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
        )
    except OSError as error:
        raise CodexExecStreamError("could not start Codex exec") from error

    thread_id: str | None = None
    turn_completed = False
    try:
        if process.stdin is None:
            raise CodexExecStreamError("Codex exec did not expose stdin")
        process.stdin.write(wrapped_prompt)
        process.stdin.close()
        if process.stdout is None:
            raise CodexExecStreamError("Codex exec did not expose stdout")
        for line in process.stdout:
            record = _parse_jsonl_record(line)
            if forward_jsonl:
                output.write(
                    json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
                )
                output.flush()
            event = _project_event(record)
            if event is None:
                continue
            if event["type"] == "thread.started":
                native_thread_id = event["thread_id"]
                assert isinstance(native_thread_id, str)
                thread_id = native_thread_id
            elif event["type"] == "turn.completed":
                turn_completed = True
            if on_event is not None:
                on_event(event)
        exit_code = process.wait()
    except BaseException:
        if process.poll() is None:
            process.terminate()
            process.wait()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()

    failure = _failure_reason(
        exit_code=exit_code,
        thread_id=thread_id,
        turn_completed=turn_completed,
    )
    return CodexExecStreamResult(
        thread_id=thread_id,
        turn_completed=turn_completed,
        exit_code=exit_code,
        protocol_complete=failure is None,
        failure=failure,
    )


def _parse_jsonl_record(line: str) -> Mapping[str, Any]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise CodexExecStreamError("Codex exec emitted malformed JSONL") from error
    if not isinstance(record, Mapping):
        raise CodexExecStreamError("Codex exec emitted a non-object JSONL record")
    return record


def _project_event(record: Mapping[str, Any]) -> dict[str, Any] | None:
    event_type = record.get("type")
    if event_type == "thread.started":
        thread_id = record.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexExecStreamError("Codex exec emitted an invalid thread.started record")
        return {"type": "thread.started", "thread_id": thread_id}
    if event_type == "turn.completed":
        return {"type": "turn.completed"}
    if event_type != "item.completed":
        return None

    item = record.get("item")
    if not isinstance(item, Mapping):
        raise CodexExecStreamError("Codex exec emitted an invalid item.completed record")
    item_type = item.get("type")
    fields = _OBSERVABLE_COMPLETED_ITEM_FIELDS.get(item_type)
    if fields is None:
        return None
    return {
        "type": "item.completed",
        "item": {field: item[field] for field in fields if field in item},
    }


def _failure_reason(
    *,
    exit_code: int,
    thread_id: str | None,
    turn_completed: bool,
) -> str | None:
    if exit_code != 0:
        return f"Codex exec exited with status {exit_code}"
    if thread_id is None:
        return "Codex exec did not emit thread.started"
    if not turn_completed:
        return "Codex exec did not emit turn.completed"
    return None
