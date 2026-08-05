from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .._util import canonical_json_bytes, sha256_hex
from ..errors import AgenticEvoError
from ..ipc import SurfaceClient


EXECUTION_SURFACE = "opencode"

# Confirmed opencode 1.18.13 hook event names (plugin event system; camelCase
# payload fields such as sessionID/messageID/info/tool/args). The pre-1.0
# hook contract names session.start / session.end (and the brief's guessed
# "notification") do NOT exist in 1.18.13; session.start / session.end are
# kept as strict aliases so callers can drive the same lifecycle.
_WAKE_EVENTS = frozenset({"session.created", "session.start"})
_SLEEP_EVENTS = frozenset({"session.idle", "session.deleted", "session.end"})
_STATUS_IDLE = frozenset({"idle"})


_SESSION_ID_FIELDS = ("sessionID", "session_id")
_PROJECT_FIELDS = ("directory", "cwd")
_MODEL_FIELDS = ("model", "modelID", "model_id")
_TOOL_NAME_FIELDS = ("tool", "tool_name")
_TOOL_CALL_ID_FIELDS = ("callID", "tool_call_id")
_TOOL_INPUT_FIELDS = ("args", "tool_input", "input")
_TOOL_RESPONSE_FIELDS = ("result", "tool_response", "response")


def handle_opencode_hook(
    home: Path,
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Map stable observable opencode hook fields without copying raw content.

    Mirrors ``handle_codex_hook``: session start/created wakes the opencode
    execution surface, session end/idle/deleted sleeps it, and every other
    confirmed event becomes a bounded, hash-only observe record. Fail-open:
    any adapter/surface error is swallowed and None is returned so the hook
    never blocks the coding agent.
    """

    if payload is None or not isinstance(payload, Mapping):
        return None

    event_name = str(_pick(payload, "type", "event", "hook_event_name") or "")
    session_id = _session_id(payload)
    project_ref = _project_ref(_project_value(payload))
    model = _model_value(payload)

    try:
        surface = SurfaceClient(Path(home))

        if event_name in _WAKE_EVENTS:
            wake = surface.wake(
                execution_surface=EXECUTION_SURFACE,
                session_id=session_id,
                project_environment=project_ref,
                model=model,
            )
            body_files = ", ".join(wake["body_files"][:16]) or "(empty body)"
            context = (
                "Agentic-Evo wake context. This opencode session is a temporary "
                "execution surface for the same user-bound Agent lineage. "
                f"Root={wake['root']}; Head={wake['head']}; "
                f"Generation={wake['generation']}; "
                f"Body files={body_files}. Treat opencode, the model, and this "
                "project as replaceable organs/environment, not as the Agent "
                "identity. No memory or learning algorithm is prescribed by "
                "this context. "
                f"Activation={wake['activation_kind']}:"
                f"{wake['activation_artifact']}@{wake['activation_digest']}. "
                "Current body activation projection follows:\n\n"
                f"{wake['activation_context']}"
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": context,
                }
            }

        if event_name in _SLEEP_EVENTS:
            surface.sleep(
                execution_surface=EXECUTION_SURFACE,
                session_id=session_id,
            )
            return None

        if event_name == "session.status" and _status_type(payload.get("status")) in _STATUS_IDLE:
            surface.sleep(
                execution_surface=EXECUTION_SURFACE,
                session_id=session_id,
            )
            return None

        event_kind, event_payload = _map_event(event_name, payload)

        surface.observe(
            event_kind=event_kind,
            execution_surface=EXECUTION_SURFACE,
            session_id=session_id or None,
            turn_id=_bounded_text(_pick(payload, "turnID", "turn_id")),
            tool_call_id=_bounded_text(_pick(payload, *_TOOL_CALL_ID_FIELDS)),
            project_environment=project_ref,
            payload={
                "hook_event": event_name,
                "model_ref": model,
                **event_payload,
            },
        )
    except (
        AgenticEvoError,
        KeyError,
        OSError,
        TimeoutError,
        TypeError,
        ValueError,
    ):
        return None
    return None


def _map_event(
    event_name: str,
    payload: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    if event_name in ("message.updated", "message"):
        info = payload.get("info")
        message_value = payload.get("message")
        if isinstance(info, Mapping):
            value: dict[str, Any] = {
                "message_role": _bounded_text(info.get("role")),
                "message_id": _bounded_text(info.get("id")),
                "message_sha256": _hash_json_value(info),
            }
        else:
            raw = str(message_value or "")
            value = {
                "message_chars": len(raw),
                "message_sha256": sha256_hex(raw),
            }
        return ("message_updated", value)
    if event_name == "message.part.updated":
        part = payload.get("part")
        return (
            "message_part_updated",
            {
                "part_type": _bounded_text(
                    part.get("type") if isinstance(part, Mapping) else None
                ),
                "part_chars": _text_chars(part),
                "part_sha256": _hash_json_value(part),
            },
        )
    if event_name == "message.part.delta":
        delta = str(payload.get("delta") or "")
        return (
            "message_part_delta",
            {
                "field": _bounded_text(payload.get("field")),
                "delta_chars": len(delta),
                "delta_sha256": sha256_hex(delta),
            },
        )
    if event_name == "message.removed":
        return (
            "message_removed",
            {"message_id": _bounded_text(payload.get("messageID"))},
        )
    if event_name == "message.part.removed":
        return (
            "message_part_removed",
            {
                "message_id": _bounded_text(payload.get("messageID")),
                "part_id": _bounded_text(payload.get("partID")),
            },
        )
    if event_name in ("tool.execute.before", "tool.execute.after", "tool"):
        value: dict[str, Any] = {
            "tool_name": _bounded_text(_pick(payload, *_TOOL_NAME_FIELDS)),
            "tool_input_sha256": _hash_json_value(
                _pick(payload, *_TOOL_INPUT_FIELDS)
            ),
        }
        if event_name == "tool.execute.after":
            value["tool_response_sha256"] = _hash_json_value(
                _pick(payload, *_TOOL_RESPONSE_FIELDS)
            )
        return (
            "tool_use_started"
            if event_name != "tool.execute.after"
            else "tool_use_finished",
            value,
        )
    if event_name in ("permission.asked", "permission.replied"):
        return (
            "permission_requested"
            if event_name == "permission.asked"
            else "permission_replied",
            {
                "tool_name": _bounded_text(_pick(payload, *_TOOL_NAME_FIELDS)),
                "tool_input_sha256": _hash_json_value(
                    _pick(payload, *_TOOL_INPUT_FIELDS)
                ),
                "permission_mode": _bounded_text(
                    _pick(payload, "permission", "mode")
                ),
            },
        )
    if event_name == "command.executed":
        arguments = str(payload.get("arguments") or "")
        return (
            "command_executed",
            {
                "command_name": _bounded_text(payload.get("name")),
                "arguments_chars": len(arguments),
                "arguments_sha256": sha256_hex(arguments),
                "message_id": _bounded_text(payload.get("messageID")),
            },
        )
    if event_name == "file.edited":
        file_path = str(_pick(payload, "path", "file_path") or "")
        return (
            "file_edited",
            {
                "file_path": _bounded_text(file_path, limit=192),
                "file_path_sha256": sha256_hex(file_path),
            },
        )
    if event_name == "session.updated":
        info = payload.get("info")
        return (
            "session_updated",
            {"session_info_sha256": _hash_json_value(info)},
        )
    if event_name == "session.compacted":
        return (
            "context_compaction_finished",
            {"trigger": _bounded_text(payload.get("reason"))},
        )
    if event_name == "session.diff":
        diff = payload.get("diff")
        return (
            "session_diff",
            {
                "diff_count": len(diff) if isinstance(diff, (list, tuple)) else 0,
                "diff_sha256": _hash_json_value(diff),
            },
        )
    if event_name == "session.status":
        status = payload.get("status")
        status_type = _status_type(status)
        value: dict[str, Any] = {"status_type": status_type}
        if isinstance(status, Mapping):
            retry_message = status.get("message")
            if retry_message is not None:
                value["status_message_sha256"] = sha256_hex(str(retry_message))
        return ("session_status", value)
    if event_name == "session.error":
        error = payload.get("error")
        error_data = error.get("data") if isinstance(error, Mapping) else None
        error_message = (
            error_data.get("message")
            if isinstance(error_data, Mapping)
            else (error.get("message") if isinstance(error, Mapping) else None)
        )
        return (
            "session_error",
            {
                "error_name": _bounded_text(
                    error.get("name") if isinstance(error, Mapping) else None
                ),
                "error_message_sha256": _hash_text(error_message),
            },
        )
    if event_name == "session.created":
        info = payload.get("info")
        return (
            "session_created",
            {"session_info_sha256": _hash_json_value(info)},
        )
    return (
        "execution_surface_event",
        {"unmapped_event_name": _bounded_text(event_name)},
    )


def _session_id(payload: Mapping[str, Any]) -> str:
    value = _pick(payload, *_SESSION_ID_FIELDS)
    if value is None:
        info = payload.get("info")
        if isinstance(info, Mapping):
            value = info.get("id")
    return str(value or "")


def _project_value(payload: Mapping[str, Any]) -> Any:
    value = _pick(payload, *_PROJECT_FIELDS)
    if value is None:
        info = payload.get("info")
        if isinstance(info, Mapping):
            value = info.get("directory")
    return value


def _model_value(payload: Mapping[str, Any]) -> str | None:
    value = _pick(payload, *_MODEL_FIELDS)
    if value is None:
        info = payload.get("info")
        if isinstance(info, Mapping):
            model = info.get("model")
            if isinstance(model, Mapping):
                value = model.get("id")
    return _bounded_text(value)


def _status_type(status: Any) -> str | None:
    if isinstance(status, Mapping):
        return _bounded_text(status.get("type"))
    return _bounded_text(status)


def _text_chars(value: Any) -> int:
    if not isinstance(value, Mapping):
        text = str(value or "")
        return len(text)
    text = value.get("text")
    if text is None:
        return 0
    return len(str(text))


def _pick(payload: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return None


def _project_ref(value: Any) -> str:
    raw = str(value or "")
    return f"sha256:{sha256_hex(raw)}"


def _hash_json_value(value: Any) -> str:
    return sha256_hex(canonical_json_bytes(value))


def _hash_text(value: Any) -> str | None:
    if value is None:
        return None
    return sha256_hex(str(value))


def _bounded_text(value: Any, limit: int = 256) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]
