"""Exact, reversible binding of one installed Agentic-Evo release to Codex.

The release lifecycle owns when this module runs and persists its returned
mapping.  This module owns only the three established Codex compatibility
launchers and exact Agentic-Evo hook command migration; it never creates a
release payload or changes unrelated Codex hooks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .activation import CodexHookTrustError, trust_installed_codex_hooks


CODEX_BINDING_SCHEMA = "agentic-evo.codex-binding.v1"
_PYZ_NAME = "agentic-evo.pyz"
_SHIM_DIRECTORY_NAME = "agentic-evo"
_SHIM_NAMES = ("hook", "cli", "run_codex")
_CODEX_HOOK_EVENTS = (
    "PermissionRequest",
    "PostCompact",
    "PostToolUse",
    "PreCompact",
    "PreToolUse",
    "SessionEnd",
    "SessionStart",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
)


class CodexBindingError(RuntimeError):
    """One exact Codex installation binding could not be completed safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def bind_codex_installation(
    program_dir: Path,
    runtime_home: Path,
    python_executable: Path,
    codex_home: Path,
    codex_executable: Path,
    prior_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Bind existing Codex hooks to the one existing installed release.

    The return value is the opaque lifecycle-owned authority for a later
    ``unbind_codex_installation`` call.  A failure rolls back this invocation's
    exact writes before raising ``CodexBindingError``; no partial binding is
    returned.
    """

    paths = _resolve_inputs(
        program_dir=program_dir,
        runtime_home=runtime_home,
        python_executable=python_executable,
        codex_home=codex_home,
        codex_executable=codex_executable,
    )
    prior = _validate_binding(prior_binding) if prior_binding is not None else None
    pyz_path = paths["program_dir"] / _PYZ_NAME
    shim_dir = paths["codex_home"] / _SHIM_DIRECTORY_NAME
    shim_paths = {
        "hook": shim_dir / "agentic-evo-hook.cmd",
        "cli": shim_dir / "agentic-evo.cmd",
        "run_codex": shim_dir / "run-codex.cmd",
    }
    desired_shims = _shim_texts(paths, pyz_path=pyz_path)
    legacy_shims = _shim_texts(paths, pyz_path=shim_dir / _PYZ_NAME)

    prior_shims = _prior_shims(prior)
    shim_records: list[dict[str, object]] = []
    migrations: list[dict[str, object]] = []
    preserved_launchers: list[str] = []
    pending_shim_writes: list[tuple[Path, str, str | None]] = []

    for name in _SHIM_NAMES:
        path = shim_paths[name]
        desired = desired_shims[name]
        legacy = legacy_shims[name]
        existing = _read_optional_text(path)
        prior_record = prior_shims.get(str(path))
        if existing is None:
            prior_content: str | None = None
            pending_shim_writes.append((path, desired, prior_content))
        elif existing == legacy:
            prior_content = existing
            pending_shim_writes.append((path, desired, prior_content))
            migrations.append(
                {
                    "path": str(path),
                    "prior_content": prior_content,
                    "replacement_content": desired,
                }
            )
        elif existing == desired:
            prior_content = _prior_content_if_matching(prior_record, desired)
        elif name == "hook":
            raise CodexBindingError("hook_launcher_modified")
        else:
            preserved_launchers.append(str(path))
            continue
        shim_records.append(
            {
                "name": name,
                "path": str(path),
                "content": desired,
                "prior_content": prior_content,
            }
        )

    if "hook" not in {record["name"] for record in shim_records}:
        raise CodexBindingError("hook_launcher_modified")

    hook_path = paths["codex_home"] / "hooks.json"
    original_hooks = _read_optional_text(hook_path)
    created_hooks_file = original_hooks is None
    if original_hooks is None:
        original_hooks = '{"hooks": {}}\n'
    legacy_command = _hook_command(paths, pyz_path=shim_dir / _PYZ_NAME)
    desired_command = _hook_command(paths, pyz_path=pyz_path)
    desired_windows_command = _quote_path(shim_paths["hook"])
    migrated_hooks, hook_entries = _migrated_hooks_text(
        original_hooks,
        legacy_command=legacy_command,
        legacy_windows_command=desired_windows_command,
        desired_command=desired_command,
        desired_windows_command=desired_windows_command,
    )
    recorded_positions = {
        (entry["event_name"], entry["group_index"], entry["handler_index"])
        for entry in hook_entries
    }
    hook_entries.extend(
        entry
        for entry in _prior_hook_entries_if_current(prior, original_hooks)
        if (entry["event_name"], entry["group_index"], entry["handler_index"])
        not in recorded_positions
    )
    hook_record = {
        "path": str(hook_path),
        "entries": hook_entries,
        "created": created_hooks_file,
    }

    changed: list[tuple[Path, str, str | None]] = []
    try:
        for path, content, prior_content in pending_shim_writes:
            _write_text(path, content)
            changed.append((path, content, prior_content))
        if original_hooks != migrated_hooks:
            _write_text(hook_path, migrated_hooks)
            changed.append(
                (hook_path, migrated_hooks, None if created_hooks_file else original_hooks)
            )
        trusted_hooks = trust_installed_codex_hooks(
            codex_executable=paths["codex_executable"],
            codex_home=paths["codex_home"],
            working_directory=paths["program_dir"],
            config_path=paths["codex_home"] / "config.toml",
            source_path=hook_path,
            command=desired_windows_command,
        )
    except CodexHookTrustError as error:
        _rollback(changed)
        raise CodexBindingError("codex_hook_trust_failed") from error
    except CodexBindingError:
        _rollback(changed)
        raise
    except OSError as error:
        _rollback(changed)
        raise CodexBindingError("binding_write_failed") from error
    except Exception:
        _rollback(changed)
        raise

    if not isinstance(trusted_hooks, int) or trusted_hooks <= 0:
        _rollback(changed)
        raise CodexBindingError("codex_hook_trust_failed")
    return {
        "schema": CODEX_BINDING_SCHEMA,
        "status": "bound",
        "program_dir": str(paths["program_dir"]),
        "runtime_home": str(paths["runtime_home"]),
        "python_executable": str(paths["python_executable"]),
        "codex_home": str(paths["codex_home"]),
        "codex_executable": str(paths["codex_executable"]),
        "pyz_path": str(pyz_path),
        "shim_paths": shim_records,
        "migrated_launcher_paths": migrations,
        "preserved_launcher_paths": preserved_launchers,
        "hook_config": hook_record,
        "trusted_hooks": trusted_hooks,
    }


def unbind_codex_installation(binding: Mapping[str, object]) -> dict[str, object]:
    """Undo only unmodified paths explicitly owned by a prior binding."""

    value = _validate_binding(binding)
    restored_launchers: list[str] = []
    removed_shims: list[str] = []
    preserved: list[str] = []
    for record in value["shim_paths"]:
        path = Path(record["path"])
        if _read_optional_text(path) == record["content"]:
            path.unlink()
            removed_shims.append(str(path))
        else:
            preserved.append(str(path))

    hook_path = Path(value["hook_config"]["path"])
    removed_hook_entries, preserved_hook_entries = _remove_owned_hook_entries(
        hook_path, value["hook_config"]["entries"]
    )
    if preserved_hook_entries:
        preserved.append(str(hook_path))
    return {
        "schema": CODEX_BINDING_SCHEMA,
        "status": "unbound",
        "removed_shim_paths": removed_shims,
        "restored_launcher_paths": restored_launchers,
        "removed_hook_entries": removed_hook_entries,
        "preserved_hook_entries": preserved_hook_entries,
        "preserved_modified_paths": preserved,
    }


def _resolve_inputs(
    *,
    program_dir: Path,
    runtime_home: Path,
    python_executable: Path,
    codex_home: Path,
    codex_executable: Path,
) -> dict[str, Path]:
    paths = {
        "program_dir": _absolute_path(program_dir),
        "runtime_home": _absolute_path(runtime_home),
        "python_executable": _absolute_path(python_executable),
        "codex_home": _absolute_path(codex_home),
        "codex_executable": _absolute_path(codex_executable),
    }
    if not paths["program_dir"].is_dir() or not (paths["program_dir"] / _PYZ_NAME).is_file():
        raise CodexBindingError("official_pyz_unavailable")
    if not paths["runtime_home"].is_dir():
        raise CodexBindingError("runtime_home_unavailable")
    if not paths["python_executable"].is_file():
        raise CodexBindingError("python_executable_unavailable")
    if not paths["codex_home"].is_dir():
        raise CodexBindingError("codex_home_unavailable")
    if not paths["codex_executable"].is_file():
        raise CodexBindingError("codex_executable_unavailable")
    return paths


def _absolute_path(value: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CodexBindingError("binding_path_not_absolute")
    return path.resolve(strict=False)


def _quote_path(path: Path) -> str:
    return '"' + str(path).replace("\\", "/") + '"'


def _hook_command(paths: Mapping[str, Path], *, pyz_path: Path) -> str:
    return " ".join(
        (
            _quote_path(paths["python_executable"]),
            _quote_path(pyz_path),
            "hook",
            "--surface",
            "codex",
            "--dev-home",
            _quote_path(paths["runtime_home"]),
            "--start-if-needed",
        )
    )


def _shim_texts(paths: Mapping[str, Path], *, pyz_path: Path) -> dict[str, str]:
    hook = _hook_command(paths, pyz_path=pyz_path)
    prefix = "@echo off\r\n"
    suffix = "\r\nexit /b %errorlevel%\r\n"
    return {
        "hook": prefix + hook + suffix,
        "cli": prefix
        + " ".join((_quote_path(paths["python_executable"]), _quote_path(pyz_path), "%*"))
        + suffix,
        "run_codex": prefix
        + " ".join(
            (
                _quote_path(paths["python_executable"]),
                _quote_path(pyz_path),
                "run-codex",
                "--dev-home",
                _quote_path(paths["runtime_home"]),
                "--codex-executable",
                _quote_path(paths["codex_executable"]),
                "%*",
            )
        )
        + suffix,
    }


def _migrated_hooks_text(
    original: str,
    *,
    legacy_command: str,
    legacy_windows_command: str,
    desired_command: str,
    desired_windows_command: str,
) -> tuple[str, list[dict[str, object]]]:
    try:
        value = json.loads(original)
    except json.JSONDecodeError as error:
        raise CodexBindingError("codex_hooks_invalid") from error
    if not isinstance(value, dict) or not isinstance(value.get("hooks"), dict):
        raise CodexBindingError("codex_hooks_invalid")
    migrated: list[dict[str, object]] = []
    bound_events: set[str] = set()
    for event_name, groups in value["hooks"].items():
        if not isinstance(groups, list):
            raise CodexBindingError("codex_hooks_invalid")
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for handler_index, handler in enumerate(group["hooks"]):
                if not isinstance(handler, dict):
                    continue
                pair = (handler.get("command"), handler.get("commandWindows"))
                if (
                    handler.get("type") == "command"
                    and pair == (legacy_command, legacy_windows_command)
                ):
                    prior_handler = _json_object_copy(handler)
                    handler["command"] = desired_command
                    handler["commandWindows"] = desired_windows_command
                    migrated.append(
                        {
                            "event_name": event_name,
                            "group_index": group_index,
                            "handler_index": handler_index,
                            "prior_handler": prior_handler,
                            "replacement_handler": _json_object_copy(handler),
                        }
                    )
                    bound_events.add(event_name)
                elif (
                    handler.get("type") == "command"
                    and pair == (desired_command, desired_windows_command)
                ):
                    bound_events.add(event_name)
    for event_name in _CODEX_HOOK_EVENTS:
        if event_name in bound_events:
            continue
        groups = value["hooks"].setdefault(event_name, [])
        if not isinstance(groups, list):
            raise CodexBindingError("codex_hooks_invalid")
        handler = {
            "type": "command",
            "command": desired_command,
            "timeout": 3 if event_name == "SessionEnd" else 30,
            "commandWindows": desired_windows_command,
        }
        group_index = len(groups)
        groups.append({"hooks": [handler]})
        migrated.append(
            {
                "event_name": event_name,
                "group_index": group_index,
                "handler_index": 0,
                "prior_handler": None,
                "replacement_handler": _json_object_copy(handler),
            }
        )
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n", migrated


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise CodexBindingError("binding_launcher_unavailable")
    return _read_required_text(path, "binding_launcher_unavailable")


def _read_required_text(path: Path, code: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CodexBindingError(code) from error


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _rollback(changed: list[tuple[Path, str, str | None]]) -> None:
    for path, expected, prior_content in reversed(changed):
        try:
            _restore_if_exact(path, expected=expected, prior_content=prior_content)
        except OSError as error:
            raise CodexBindingError("binding_rollback_failed") from error


def _restore_if_exact(
    path: Path, *, expected: str, prior_content: str | None
) -> str:
    current = _read_optional_text(path)
    if current != expected:
        return "preserved"
    if prior_content is None:
        path.unlink()
        return "removed"
    _write_text(path, prior_content)
    return "restored"


def _prior_shims(
    binding: Mapping[str, object] | None,
) -> dict[str, Mapping[str, object]]:
    if binding is None:
        return {}
    return {str(item["path"]): item for item in binding["shim_paths"]}


def _prior_content_if_matching(
    record: Mapping[str, object] | None, current: str
) -> str | None:
    if record is None or record.get("content", record.get("replacement_content")) != current:
        return current
    prior = record.get("prior_content")
    return prior if isinstance(prior, str) else None


def _json_object_copy(value: Mapping[str, object]) -> dict[str, object]:
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise CodexBindingError("codex_hooks_invalid") from error
    if not isinstance(copied, dict):
        raise CodexBindingError("codex_hooks_invalid")
    return copied


def _prior_hook_entries_if_current(
    binding: Mapping[str, object] | None, current: str
) -> list[dict[str, object]]:
    if binding is None:
        return []
    try:
        value = json.loads(current)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, dict):
        return []
    entries = binding["hook_config"]["entries"]
    return [
        _json_object_copy(entry)
        for entry in entries
        if _owned_hook_entry_matches(value, entry)
    ]


def _owned_hook_entry_matches(
    value: Mapping[str, object], entry: Mapping[str, object]
) -> bool:
    hooks = value.get("hooks")
    if not isinstance(hooks, Mapping):
        return False
    groups = hooks.get(entry["event_name"])
    if not isinstance(groups, list):
        return False
    group_index = entry["group_index"]
    handler_index = entry["handler_index"]
    if not isinstance(group_index, int) or not isinstance(handler_index, int):
        return False
    if group_index < 0 or group_index >= len(groups):
        return False
    group = groups[group_index]
    if not isinstance(group, Mapping):
        return False
    handlers = group.get("hooks")
    if not isinstance(handlers, list) or handler_index < 0 or handler_index >= len(handlers):
        return False
    return handlers[handler_index] == entry["replacement_handler"]


def _remove_owned_hook_entries(path: Path, entries: list[dict[str, object]]) -> tuple[int, int]:
    if not entries:
        return 0, 0
    current = _read_optional_text(path)
    if current is None:
        return 0, len(entries)
    try:
        value = json.loads(current)
    except json.JSONDecodeError:
        return 0, len(entries)
    if not isinstance(value, dict) or not isinstance(value.get("hooks"), dict):
        return 0, len(entries)

    grouped: dict[str, dict[int, list[dict[str, object]]]] = {}
    for entry in entries:
        event_name = entry["event_name"]
        group_index = entry["group_index"]
        grouped.setdefault(event_name, {}).setdefault(group_index, []).append(entry)

    removed = 0
    preserved = 0
    for event_name, by_group in grouped.items():
        groups = value["hooks"].get(event_name)
        if not isinstance(groups, list):
            preserved += sum(len(items) for items in by_group.values())
            continue
        for group_index in sorted(by_group, reverse=True):
            records = by_group[group_index]
            if group_index < 0 or group_index >= len(groups):
                preserved += len(records)
                continue
            group = groups[group_index]
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                preserved += len(records)
                continue
            handlers = group["hooks"]
            for entry in sorted(records, key=lambda item: item["handler_index"], reverse=True):
                handler_index = entry["handler_index"]
                if (
                    handler_index < 0
                    or handler_index >= len(handlers)
                    or handlers[handler_index] != entry["replacement_handler"]
                ):
                    preserved += 1
                    continue
                del handlers[handler_index]
                removed += 1
            if not handlers:
                groups.pop(group_index)
    if removed:
        _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return removed, preserved


def _validate_binding(binding: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(binding, Mapping):
        raise CodexBindingError("invalid_binding")
    value = dict(binding)
    if value.get("schema") != CODEX_BINDING_SCHEMA or value.get("status") != "bound":
        raise CodexBindingError("invalid_binding")
    for field in (
        "program_dir",
        "runtime_home",
        "python_executable",
        "codex_home",
        "codex_executable",
        "pyz_path",
    ):
        if not isinstance(value.get(field), str) or not value[field]:
            raise CodexBindingError("invalid_binding")
    shims = value.get("shim_paths")
    if not isinstance(shims, list) or not shims:
        raise CodexBindingError("invalid_binding")
    normalized_shims: list[dict[str, object]] = []
    names: set[str] = set()
    paths: set[str] = set()
    for item in shims:
        if not isinstance(item, Mapping):
            raise CodexBindingError("invalid_binding")
        name = item.get("name")
        path = item.get("path")
        content = item.get("content")
        prior_content = item.get("prior_content")
        if (
            not isinstance(name, str)
            or name not in _SHIM_NAMES
            or name in names
            or not isinstance(path, str)
            or not path
            or path in paths
            or not isinstance(content, str)
            or not content
            or prior_content is not None and not isinstance(prior_content, str)
        ):
            raise CodexBindingError("invalid_binding")
        names.add(name)
        paths.add(path)
        normalized_shims.append(
            {
                "name": name,
                "path": path,
                "content": content,
                "prior_content": prior_content,
            }
        )
    hook_config = value.get("hook_config")
    if not isinstance(hook_config, Mapping):
        raise CodexBindingError("invalid_binding")
    hook_path = hook_config.get("path")
    entries = hook_config.get("entries")
    created = hook_config.get("created")
    if (
        not isinstance(hook_path, str)
        or not hook_path
        or not isinstance(entries, list)
        or not isinstance(created, bool)
    ):
        raise CodexBindingError("invalid_binding")
    normalized_entries: list[dict[str, object]] = []
    entry_positions: set[tuple[str, int, int]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise CodexBindingError("invalid_binding")
        event_name = entry.get("event_name")
        group_index = entry.get("group_index")
        handler_index = entry.get("handler_index")
        prior_handler = entry.get("prior_handler")
        replacement_handler = entry.get("replacement_handler")
        if (
            not isinstance(event_name, str)
            or not event_name
            or not isinstance(group_index, int)
            or group_index < 0
            or not isinstance(handler_index, int)
            or handler_index < 0
            or prior_handler is not None and not isinstance(prior_handler, Mapping)
            or not isinstance(replacement_handler, Mapping)
        ):
            raise CodexBindingError("invalid_binding")
        position = (event_name, group_index, handler_index)
        if position in entry_positions:
            raise CodexBindingError("invalid_binding")
        entry_positions.add(position)
        normalized_entries.append(
            {
                "event_name": event_name,
                "group_index": group_index,
                "handler_index": handler_index,
                "prior_handler": (
                    None
                    if prior_handler is None
                    else _json_object_copy(prior_handler)
                ),
                "replacement_handler": _json_object_copy(replacement_handler),
            }
        )
    normalized_hook_config = {
        "path": hook_path,
        "entries": normalized_entries,
        "created": created,
    }
    migrated_paths = value.get("migrated_launcher_paths")
    preserved_paths = value.get("preserved_launcher_paths")
    trusted_hooks = value.get("trusted_hooks")
    if (
        not isinstance(migrated_paths, list)
        or not isinstance(preserved_paths, list)
        or any(not isinstance(path, str) or not path for path in preserved_paths)
        or not isinstance(trusted_hooks, int)
        or trusted_hooks <= 0
    ):
        raise CodexBindingError("invalid_binding")
    for item in migrated_paths:
        if not isinstance(item, Mapping):
            raise CodexBindingError("invalid_binding")
        if any(
            not isinstance(item.get(field), str) or not item[field]
            for field in ("path", "prior_content", "replacement_content")
        ):
            raise CodexBindingError("invalid_binding")
    value["shim_paths"] = normalized_shims
    value["hook_config"] = normalized_hook_config
    return value
