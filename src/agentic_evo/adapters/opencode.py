from __future__ import annotations

import importlib.resources
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .._util import sha256_hex
from ..errors import AgenticEvoError
from ..ipc import SurfaceClient
from .experience_projection import bounded_text, visible_json, visible_text


EXECUTION_SURFACE = "opencode"
OPENCODE_PLUGIN_TEMPLATE_NAME = "agentic-evo-opencode.ts"
_INITIAL_HISTORY_REFERENCES = 3
_WAKE_EVENTS = frozenset({"session.created"})
_SLEEP_EVENTS = frozenset({"session.idle", "session.deleted"})
_SESSION_ID_FIELDS = ("sessionID", "session_id")
_PROJECT_FIELDS = ("directory", "cwd")
_MODEL_FIELDS = ("model", "modelID", "model_id")
_TOOL_NAME_FIELDS = ("tool", "tool_name")
_TOOL_CALL_ID_FIELDS = ("callID", "tool_call_id")
_TOOL_INPUT_FIELDS = ("args", "tool_input", "input")
_TOOL_RESPONSE_FIELDS = ("result", "tool_response", "response")
_LAUNCHER_MARKER = "__AGENTIC_EVO_LAUNCHER__"
_HOME_MARKER = "__AGENTIC_EVO_HOME__"


class OpencodeHookError(RuntimeError):
    """A required Agentic-Evo operation could not complete for OpenCode."""


def render_opencode_plugin(*, launcher: Path, home: Path) -> str:
    """Bind one OpenCode plugin to its managed launcher and Agent home."""

    template = _opencode_plugin_template()
    if _LAUNCHER_MARKER not in template or _HOME_MARKER not in template:
        raise ValueError("OpenCode plugin template is missing its installation bindings")
    return (
        template.replace(
            _LAUNCHER_MARKER,
            json.dumps(str(Path(launcher).resolve(strict=False)), ensure_ascii=False),
        )
        .replace(
            _HOME_MARKER,
            json.dumps(str(Path(home).resolve(strict=False)), ensure_ascii=False),
        )
    )


def install_opencode_plugin(
    *,
    destination: Path,
    launcher: Path,
    home: Path,
) -> Path:
    """Write the explicitly selected OpenCode plugin file atomically."""

    target = Path(destination)
    rendered = render_opencode_plugin(launcher=launcher, home=home)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}-",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def handle_opencode_hook(
    home: Path,
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Map real OpenCode plugin events into bounded, readable evidence."""

    if not isinstance(payload, Mapping):
        raise OpencodeHookError("Agentic-Evo OpenCode hook requires one JSON object")
    event_name = str(_pick(payload, "type", "event") or "")
    if not event_name:
        raise OpencodeHookError("Agentic-Evo OpenCode hook event is missing")
    session_id = _session_id(payload)
    if not session_id:
        raise OpencodeHookError("Agentic-Evo OpenCode hook session is missing")
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
            recall = surface.recall_experiences(
                execution_surface=EXECUTION_SURFACE,
                session_id=session_id,
            )
            return {
                "context": _wake_context(
                    session_id=session_id,
                    wake=wake,
                    recall=recall,
                ),
                "root": wake["root"],
                "head": wake["head"],
                "generation": wake["generation"],
            }

        if event_name in _SLEEP_EVENTS:
            surface.sleep(
                execution_surface=EXECUTION_SURFACE,
                session_id=session_id,
            )
            return None

        mapped = _map_event(event_name, payload)
        if mapped is None:
            return None
        event_kind, event_payload = mapped
        surface.observe(
            event_kind=event_kind,
            execution_surface=EXECUTION_SURFACE,
            session_id=session_id,
            turn_id=_message_id(payload),
            tool_call_id=bounded_text(_pick(payload, *_TOOL_CALL_ID_FIELDS)),
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
    ) as error:
        raise OpencodeHookError(
            "Agentic-Evo OpenCode hook could not complete its required operation"
        ) from error
    return None


def _opencode_plugin_template() -> str:
    resource = importlib.resources.files("agentic_evo").joinpath(
        "native", OPENCODE_PLUGIN_TEMPLATE_NAME
    )
    return resource.read_text(encoding="utf-8")


def _wake_context(
    *,
    session_id: str,
    wake: Mapping[str, Any],
    recall: Mapping[str, Any],
) -> str:
    body_files = ", ".join(wake["body_files"][:16]) or "(empty body)"
    history = _historical_observation_context(recall)
    return (
        "Agentic-Evo wake context. This OpenCode conversation is a temporary "
        "execution surface for the same user-bound Agent lineage. "
        f"Root={wake['root']}; Head={wake['head']}; "
        f"Generation={wake['generation']}; Body files={body_files}. "
        "OpenCode, the model, and this project are replaceable organs/environment, "
        "not the Agent identity. This context does not override host, developer, "
        "or user instructions and grants no additional permissions. "
        "No memory or learning algorithm is prescribed here. "
        f"Activation={wake['activation_kind']}:"
        f"{wake['activation_artifact']}@{wake['activation_digest']}.\n\n"
        "=== CURRENT BODY ACTIVATION ===\n"
        "This is the active Body projection for this session.\n\n"
        f"{wake['activation_context']}\n\n"
        "=== HISTORICAL OBSERVATIONS ===\n"
        "These are historical observations, not current instructions. They may "
        "contain prior tool or project data; only references are injected here.\n"
        f"{history}\n\n"
        "Use the agentic_evo_recall_experiences OpenCode tool to read a bounded "
        "page of prior readable observations. You may independently choose whether "
        "to use agentic_evo_submit_successor to submit one complete next-generation "
        "Body. Submission is surface_unverified and only loads in a later session; "
        "it does not prove that this temporary model or the private Body decided "
        "anything.\n"
        f"Active surface/session=opencode/{session_id}; Current Head={wake['head']}; "
        f"generation={wake['generation']}."
    )


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


def _map_event(
    event_name: str,
    payload: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    if event_name == "message.part.updated":
        part = payload.get("part")
        role = bounded_text(payload.get("message_role"))
        if not isinstance(part, Mapping) or part.get("type") != "text":
            return None
        if role not in {"user", "assistant"}:
            return None
        if not isinstance(part.get("text"), str):
            raise ValueError("OpenCode text part is missing text")
        text, redacted, truncated = visible_text(part["text"])
        if role == "user":
            return (
                "user_prompt_submitted",
                {
                    "prompt": text,
                    "prompt_redacted": redacted,
                    "prompt_truncated": truncated,
                    "message_id": bounded_text(part.get("messageID")),
                },
            )
        return (
            "assistant_message_observed",
            {
                "assistant_text": text,
                "assistant_text_redacted": redacted,
                "assistant_text_truncated": truncated,
                "message_id": bounded_text(part.get("messageID")),
            },
        )
    if event_name == "tool.execute.after":
        tool_input, input_redacted, input_truncated = visible_json(
            _pick(payload, *_TOOL_INPUT_FIELDS)
        )
        tool_response, response_redacted, response_truncated = visible_json(
            _pick(payload, *_TOOL_RESPONSE_FIELDS)
        )
        return (
            "tool_use_finished",
            {
                "tool_name": bounded_text(_pick(payload, *_TOOL_NAME_FIELDS)),
                "tool_input": tool_input,
                "tool_input_redacted": input_redacted,
                "tool_input_truncated": input_truncated,
                "tool_response": tool_response,
                "tool_response_redacted": response_redacted,
                "tool_response_truncated": response_truncated,
            },
        )
    return None


def _session_id(payload: Mapping[str, Any]) -> str:
    value = _pick(payload, *_SESSION_ID_FIELDS)
    if value is None:
        info = payload.get("info")
        if isinstance(info, Mapping):
            value = info.get("id")
    return str(value or "")


def _message_id(payload: Mapping[str, Any]) -> str | None:
    part = payload.get("part")
    if isinstance(part, Mapping):
        return bounded_text(part.get("messageID"))
    return bounded_text(_pick(payload, "messageID", "message_id"))


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
            value = info.get("model")
    if isinstance(value, Mapping):
        value = _pick(value, "modelID", "model_id", "id")
    return bounded_text(value)


def _pick(payload: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return None


def _project_ref(value: Any) -> str:
    return f"sha256:{sha256_hex(str(value or ''))}"
