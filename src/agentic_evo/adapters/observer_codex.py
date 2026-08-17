from __future__ import annotations

import re
from typing import Any, Mapping

from .._util import canonical_json_bytes, sha256_hex


_LIFECYCLE_EVENTS = {"SessionStart", "SessionEnd", "PreToolUse", "UserPromptSubmit", "Stop"}
_FAILED_STATUSES = {"failed", "failure", "error"}


def adapt_codex_record(record: Mapping[str, Any], *, locator: str, sequence: int):
    from ..observer import OBSERVER_SCHEMA_VERSION, SessionEvent, SessionKey

    hook = record.get("hook_event_name")
    if hook in _LIFECYCLE_EVENTS:
        return _event(
            record,
            locator,
            sequence,
            event_kind="lifecycle",
            action="lifecycle",
            status="unknown",
            exit_code=None,
            coverage_gap="Codex lifecycle event has no structured command result.",
        )
    if hook != "PostToolUse" and record.get("event_kind") not in {
        "command_result",
        "test_result",
        "tool_result",
    }:
        return None
    result = record.get("tool_response") if hook == "PostToolUse" else record
    result = result if isinstance(result, Mapping) else {}
    exit_code = _exit_code(result.get("exit_code"))
    status = _status(result.get("status"), exit_code)
    action = _action(record.get("tool_name"), record.get("event_kind"))
    gap = None if status != "unknown" else "Codex PostToolUse lacked structured status and exit code."
    return _event(record, locator, sequence, "tool_result", action, status, exit_code, gap)


def _event(record, locator, sequence, event_kind, action, status, exit_code, coverage_gap):
    from ..observer import OBSERVER_SCHEMA_VERSION, SessionEvent, SessionKey

    return SessionEvent(
        schema_version=OBSERVER_SCHEMA_VERSION,
        session_key=SessionKey("codex", "codex", _bounded_id(record.get("session_id"))),
        sequence=sequence,
        occurred_at=_bounded_time(record.get("occurred_at") or record.get("timestamp")),
        event_kind=event_kind,
        actor="coding_agent",
        action=action,
        status=status,
        exit_code=exit_code,
        duration_ms=_duration(record.get("duration_ms")),
        safe_summary=f"codex {action} status={status}",
        source_locator=locator,
        raw_fingerprint="sha256:" + sha256_hex(canonical_json_bytes(record)),
        coverage_gap=coverage_gap,
    )


def _action(tool_name: Any, event_kind: Any) -> str:
    text = str(tool_name or event_kind or "").lower()
    return "test" if text in {"test", "test_result", "pytest", "unittest"} else "command"


def _status(value: Any, exit_code: int | None) -> str:
    if exit_code is not None:
        return "failed" if exit_code != 0 else "succeeded"
    text = str(value or "").lower()
    if text in _FAILED_STATUSES:
        return "failed"
    if text in {"passed", "success", "succeeded", "ok"}:
        return "succeeded"
    return "unknown"


def _exit_code(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _duration(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _bounded_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", text):
        return text
    return "sha256:" + sha256_hex(text)[:16]


def _bounded_time(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if re.fullmatch(r"[0-9TZ:+.\-]{1,64}", text) else None
