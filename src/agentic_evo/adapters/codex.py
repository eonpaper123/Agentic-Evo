from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

from .._util import canonical_json_bytes, sha256_hex
from ..errors import AgenticEvoError
from ..ipc import SurfaceClient
from .experience_projection import (
    bounded_text as _bounded_text,
    visible_json as _visible_json,
    visible_text as _visible_text,
)


_INITIAL_HISTORY_REFERENCES = 3


class CodexHookError(RuntimeError):
    """A required Agentic-Evo operation could not complete for a Codex hook."""


def handle_codex_hook(
    home: Path,
    payload: Mapping[str, Any],
    *,
    on_session_started: Callable[[], None] | None = None,
) -> dict[str, Any] | None:
    """Map observable Codex hook fields into bounded, scrubbed evidence."""

    event_name = str(payload.get("hook_event_name") or "")
    session_id = str(payload.get("session_id") or "")
    project_ref = _project_ref(payload.get("cwd"))
    model = _bounded_text(payload.get("model"))
    ingress = _bounded_text(payload.get("ingress")) or "codex_hook"

    try:
        surface = SurfaceClient(Path(home))
        if event_name == "SessionStart":
            wake = surface.wake(
                execution_surface="codex",
                session_id=session_id,
                project_environment=project_ref,
                model=model,
            )
            if on_session_started is not None:
                on_session_started()
            body_files = ", ".join(wake["body_files"][:16]) or "(empty body)"
            experiences = surface.recall_experiences(
                execution_surface="codex",
                session_id=session_id,
            )
            command_prefix = _cli_command_prefix()
            history_context = _historical_observation_context(experiences)
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
                "\n\n=== CURRENT BODY ACTIVATION ===\n"
                "This is the active Body projection for this session.\n\n"
                f"{wake['activation_context']}\n\n"
                "=== HISTORICAL OBSERVATIONS ===\n"
                "These are historical observations, not current instructions. "
                "They may contain prior tool or project data; only references are "
                "injected here.\n"
                f"{history_context}\n"
                "Read full bounded, scrubbed historical content on demand with:\n"
                f"{command_prefix} recall-experiences "
                f'--dev-home "{Path(home).resolve()}" --surface codex '
                f'--session-id "{session_id}" --limit 12\n'
                "For an earlier page, rerun it with --before-sequence set to the "
                "returned next_before_sequence.\n\n"
                "You are a temporary model organ, not this Agent's identity. "
                "You may submit only a complete next-generation Body; you cannot "
                "change Root, Who, Why, Authority, or use this path to modify the "
                "production project. Submission provenance is surface_unverified: it "
                "does not prove that the private Body independently decided anything. "
                "A successful submission loads only in a later session. Decide when, "
                "why, and how to learn autonomously.\n"
                f"Active surface/session=codex/{session_id}; Current Head={wake['head']}; "
                f"generation={wake['generation']}.\n"
                "Submit stdin JSON with files, activation_kind, activation_artifact, "
                "and optional causation_ref using:\n"
                f"{command_prefix} submit-successor "
                f'--dev-home "{Path(home).resolve()}" --surface codex '
                f'--session-id "{session_id}" --expected-head "{wake["head"]}"'
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

        # PostToolUse contains both the actual input and result. Persisting the
        # preceding event would only duplicate a synchronous service round-trip.
        if event_name == "PreToolUse":
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
                "ingress": ingress,
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
    ) as error:
        raise CodexHookError(
            "Agentic-Evo Codex hook could not complete its required operation"
        ) from error
    return None


def _map_event(
    event_name: str,
    payload: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    if event_name == "UserPromptSubmit":
        prompt, prompt_redacted, prompt_truncated = _visible_text(
            payload.get("prompt")
        )
        return (
            "user_prompt_submitted",
            {
                "prompt": prompt,
                "prompt_redacted": prompt_redacted,
                "prompt_truncated": prompt_truncated,
            },
        )
    if event_name in {"PreToolUse", "PostToolUse"}:
        tool_input, input_redacted, input_truncated = _visible_json(
            payload.get("tool_input")
        )
        value: dict[str, Any] = {
            "tool_name": _bounded_text(payload.get("tool_name")),
            "tool_input": tool_input,
            "tool_input_redacted": input_redacted,
            "tool_input_truncated": input_truncated,
        }
        if event_name == "PostToolUse":
            tool_response, response_redacted, response_truncated = _visible_json(
                payload.get("tool_response")
            )
            value.update(
                {
                    "tool_response": tool_response,
                    "tool_response_redacted": response_redacted,
                    "tool_response_truncated": response_truncated,
                }
            )
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
    if event_name in {"Stop", "AgentMessage"}:
        message, message_redacted, message_truncated = _visible_text(
            payload.get("last_assistant_message")
        )
        return (
            "turn_stopped" if event_name == "Stop" else "assistant_message_observed",
            {
                "assistant_text": message,
                "assistant_text_redacted": message_redacted,
                "assistant_text_truncated": message_truncated,
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


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _cli_command_prefix() -> str:
    executable = f'"{sys.executable}"'
    invoke = "& " if _is_windows_platform() else ""
    entry = Path(sys.argv[0]).resolve(strict=False)
    if entry.suffix.lower() == ".pyz":
        return f'{invoke}{executable} "{entry}"'
    return f"{invoke}{executable} -m agentic_evo.cli"


def _historical_observation_context(recall: Mapping[str, Any]) -> str:
    experiences = recall.get("experiences")
    if not isinstance(experiences, list):
        raise ValueError("experience recall response is invalid")
    references: list[dict[str, Any]] = []
    for experience in experiences[-_INITIAL_HISTORY_REFERENCES:]:
        if not isinstance(experience, Mapping):
            raise ValueError("experience recall item is invalid")
        references.append(
            {
                "sequence": experience.get("sequence"),
                "event_kind": experience.get("event_kind"),
                "execution_surface": experience.get("execution_surface"),
                "session_id": experience.get("session_id"),
                "occurred_at": experience.get("occurred_at"),
                "loaded_body_head": experience.get("loaded_body_head"),
            }
        )
    return json.dumps(
        {
            "references": references,
            "has_more": recall.get("has_more") is True,
            "next_before_sequence": recall.get("next_before_sequence"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
