from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .._util import canonical_json_bytes, sha256_hex
from ..errors import AgenticEvoError
from ..ipc import SurfaceClient


def handle_codex_hook(
    home: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Map stable observable Codex hook fields without copying raw prompt/tool content."""

    event_name = str(payload.get("hook_event_name") or "")
    session_id = str(payload.get("session_id") or "")
    project_ref = _project_ref(payload.get("cwd"))
    model = _bounded_text(payload.get("model"))

    try:
        surface = SurfaceClient(Path(home))
        if event_name == "SessionStart":
            wake = surface.wake(
                execution_surface="codex",
                session_id=session_id,
                project_environment=project_ref,
                model=model,
            )
            body_files = ", ".join(wake["body_files"][:16]) or "(empty body)"
            context = (
                "Agentic-Evo wake context. This Codex conversation is a temporary "
                "execution surface for the same user-bound Agent lineage. "
                f"Root={wake['root']}; Head={wake['head']}; "
                f"Generation={wake['generation']}; "
                f"Body files={body_files}. Treat Codex, the model, and this project "
                "as replaceable organs/environment, not as the Agent identity. "
                "No memory or learning algorithm is prescribed by this context. "
                f"Activation={wake['activation_kind']}:"
                f"{wake['activation_artifact']}@{wake['activation_digest']}. "
                "Current body activation projection follows:\n\n"
                f"{wake['activation_context']}"
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }

        if event_name == "SessionEnd":
            surface.sleep(
                execution_surface="codex",
                session_id=session_id,
            )
            return None

        event_kind, event_payload = _map_event(event_name, payload)
        surface.observe(
            event_kind=event_kind,
            execution_surface="codex",
            session_id=session_id or None,
            turn_id=_bounded_text(payload.get("turn_id")),
            tool_call_id=_bounded_text(payload.get("tool_use_id")),
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
    if event_name == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        return (
            "user_prompt_submitted",
            {
                "prompt_chars": len(prompt),
                "prompt_sha256": sha256_hex(prompt),
            },
        )
    if event_name in {"PreToolUse", "PostToolUse"}:
        tool_input = payload.get("tool_input")
        tool_response = payload.get("tool_response")
        value: dict[str, Any] = {
            "tool_name": _bounded_text(payload.get("tool_name")),
            "tool_input_sha256": _hash_json_value(tool_input),
        }
        if event_name == "PostToolUse":
            value["tool_response_sha256"] = _hash_json_value(tool_response)
        return (
            "tool_use_started" if event_name == "PreToolUse" else "tool_use_finished",
            value,
        )
    if event_name in {"PreCompact", "PostCompact"}:
        return (
            "context_compaction_started"
            if event_name == "PreCompact"
            else "context_compaction_finished",
            {"trigger": _bounded_text(payload.get("trigger"))},
        )
    if event_name in {"SubagentStart", "SubagentStop"}:
        return (
            "subagent_started" if event_name == "SubagentStart" else "subagent_stopped",
            {
                "agent_id": _bounded_text(payload.get("agent_id")),
                "agent_type": _bounded_text(payload.get("agent_type")),
                "last_message_sha256": _hash_text(
                    payload.get("last_assistant_message")
                ),
            },
        )
    if event_name == "Stop":
        message = str(payload.get("last_assistant_message") or "")
        return (
            "turn_stopped",
            {
                "last_message_chars": len(message),
                "last_message_sha256": sha256_hex(message),
            },
        )
    if event_name == "PermissionRequest":
        return (
            "permission_requested",
            {
                "tool_name": _bounded_text(payload.get("tool_name")),
                "tool_input_sha256": _hash_json_value(payload.get("tool_input")),
                "permission_mode": _bounded_text(payload.get("permission_mode")),
            },
        )
    return (
        "execution_surface_event",
        {"unmapped_event_name": _bounded_text(event_name)},
    )


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
