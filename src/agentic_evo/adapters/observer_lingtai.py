from __future__ import annotations

import re
from typing import Any, Mapping

from .._util import canonical_json_bytes, sha256_hex


_LIFECYCLE_EVENTS = {"daemon_started", "daemon_stopped", "heartbeat", "session_started", "session_ended"}
_RESULT_EVENTS = {"command_result", "test_result", "tool_result"}


def adapt_lingtai_record(record: Mapping[str, Any], *, locator: str, sequence: int):
    from ..observer import OBSERVER_SCHEMA_VERSION, SessionEvent, SessionKey

    event_name = record.get("event_kind") or record.get("type")
    if event_name in _LIFECYCLE_EVENTS:
        action, status = "lifecycle", "unknown"
        gap = "LingTai lifecycle-only event cannot establish a coding result."
        exit_code = None
        kind = "lifecycle"
    elif event_name in _RESULT_EVENTS:
        exit_code = _exit_code(record.get("exit_code"))
        status = _status(record.get("status"), exit_code)
        action = "test" if event_name == "test_result" else "command"
        gap = None if status != "unknown" else "LingTai result lacked structured status and exit code."
        kind = "tool_result"
    else:
        return None
    surface = _bounded(record.get("execution_surface") or "lingtai", 128) or "lingtai"
    return SessionEvent(
        schema_version=OBSERVER_SCHEMA_VERSION,
        session_key=SessionKey("lingtai", surface, _bounded(record.get("session_id"), 128)),
        sequence=sequence,
        occurred_at=_bounded(record.get("occurred_at") or record.get("timestamp"), 64),
        event_kind=kind,
        actor="coding_agent",
        action=action,
        status=status,
        exit_code=exit_code,
        duration_ms=_duration(record.get("duration_ms")),
        safe_summary=f"lingtai {action} status={status}",
        source_locator=locator,
        raw_fingerprint="sha256:" + sha256_hex(canonical_json_bytes(record)),
        coverage_gap=gap,
    )


def _status(value: Any, exit_code: int | None) -> str:
    if exit_code is not None:
        return "failed" if exit_code != 0 else "succeeded"
    text = str(value or "").lower()
    if text in {"failed", "failure", "error"}:
        return "failed"
    if text in {"passed", "success", "succeeded", "ok"}:
        return "succeeded"
    return "unknown"


def _exit_code(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _duration(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _bounded(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if re.fullmatch(rf"[A-Za-z0-9_.:-]{{1,{limit}}}", text):
        return text
    return "sha256:" + sha256_hex(text)[:16]
