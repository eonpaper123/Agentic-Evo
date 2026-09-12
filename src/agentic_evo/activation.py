from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence

from .ipc import ServiceNotRunningError, SurfaceClient


_STARTUP_TIMEOUT_SECONDS = 15.0
_STARTUP_POLL_SECONDS = 0.1
_CODEX_HOOK_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_APP_SERVER_RESPONSE_TIMEOUT_SECONDS = 15.0
REQUIRED_CODEX_HOOK_EVENT_NAMES = frozenset(
    {
        "permissionRequest",
        "postCompact",
        "postToolUse",
        "preCompact",
        "preToolUse",
        "sessionEnd",
        "sessionStart",
        "stop",
        "subagentStart",
        "subagentStop",
        "userPromptSubmit",
    }
)


class CodexHookTrustError(RuntimeError):
    """Codex did not confirm trust for this activation's hooks."""


@dataclass(frozen=True)
class CodexHookTarget:
    key: str
    current_hash: str
    trust_status: str | None
    event_name: str


def select_exact_codex_hooks(
    hooks: Sequence[Mapping[str, Any]],
    *,
    source_path: Path | str,
    command: str,
) -> tuple[CodexHookTarget, ...]:
    """Select only enabled hooks created by this exact activation command."""

    expected_source = _normalized_path(source_path)
    selected: list[CodexHookTarget] = []
    seen: set[str] = set()
    for hook in hooks:
        actual_source = hook.get("sourcePath")
        if not isinstance(actual_source, str) or _normalized_path(actual_source) != expected_source:
            continue
        if hook.get("command") != command or hook.get("enabled") is not True:
            continue
        key = hook.get("key")
        current_hash = hook.get("currentHash")
        trust_status = hook.get("trustStatus")
        event_name = hook.get("eventName")
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(current_hash, str)
            or _CODEX_HOOK_HASH.fullmatch(current_hash) is None
            or (trust_status is not None and not isinstance(trust_status, str))
            or not isinstance(event_name, str)
            or not event_name
            or key in seen
        ):
            raise CodexHookTrustError("Codex returned an invalid matching hook")
        selected.append(
            CodexHookTarget(
                key=key,
                current_hash=current_hash,
                trust_status=trust_status,
                event_name=event_name,
            )
        )
        seen.add(key)
    if not selected:
        raise CodexHookTrustError(
            "Codex did not list an enabled hook for this activation command"
        )
    return tuple(sorted(selected, key=lambda target: target.key))


def trusted_hash_edits(
    targets: Sequence[CodexHookTarget],
) -> list[dict[str, str]]:
    """Build the exact config edits accepted by Codex config/batchWrite."""

    return [
        {
            "keyPath": (
                f'hooks.state."{target.key.replace("\\", "\\\\").replace(chr(34), chr(92) + chr(34))}".'
                "trusted_hash"
            ),
            "mergeStrategy": "replace",
            "value": target.current_hash,
        }
        for target in targets
    ]


def verify_exact_codex_hook_trust(
    hooks: Sequence[Mapping[str, Any]],
    *,
    expected: Sequence[CodexHookTarget],
    source_path: Path | str,
    command: str,
) -> None:
    """Require a fresh Codex hook list to match and trust every target."""

    actual = select_exact_codex_hooks(
        hooks,
        source_path=source_path,
        command=command,
    )
    if tuple(
        (target.key, target.current_hash, target.event_name) for target in actual
    ) != tuple(
        (target.key, target.current_hash, target.event_name) for target in expected
    ):
        raise CodexHookTrustError(
            "Codex hook set changed before trust verification"
        )
    if any(target.trust_status != "trusted" for target in actual):
        raise CodexHookTrustError(
            "Codex did not mark every activated hook as trusted"
        )


def trust_installed_codex_hooks(
    *,
    codex_executable: Path,
    codex_home: Path,
    working_directory: Path,
    config_path: Path,
    source_path: Path,
    command: str,
) -> int:
    """Trust only this activation's live Codex hook hashes and recheck them."""

    with _CodexHookTrustProtocol(
        codex_executable=codex_executable,
        codex_home=codex_home,
        working_directory=working_directory,
    ) as protocol:
        expected = select_exact_codex_hooks(
            protocol.list_hooks(working_directory),
            source_path=source_path,
            command=command,
        )
        _require_all_codex_hook_events(expected)
        protocol.write_trusted_hashes(
            trusted_hash_edits(expected),
            config_path=config_path,
        )
        fresh_hooks = protocol.list_hooks(working_directory)
    verify_exact_codex_hook_trust(
        fresh_hooks,
        expected=expected,
        source_path=source_path,
        command=command,
    )
    _require_all_codex_hook_events(
        select_exact_codex_hooks(
            fresh_hooks,
            source_path=source_path,
            command=command,
        )
    )
    return len(expected)


class _CodexHookTrustProtocol:
    """The three app-server calls required by activate-codex."""

    def __init__(
        self,
        *,
        codex_executable: Path,
        codex_home: Path,
        working_directory: Path,
    ) -> None:
        self._codex_executable = Path(codex_executable)
        self._codex_home = Path(codex_home)
        self._working_directory = Path(working_directory)
        self._process: subprocess.Popen[str] | None = None
        self._lines: Queue[str | None] = Queue()
        self._next_request_id = 1

    def __enter__(self) -> "_CodexHookTrustProtocol":
        try:
            self._process = subprocess.Popen(
                [
                    str(self._codex_executable),
                    "app-server",
                    "--listen",
                    "stdio://",
                ],
                cwd=self._working_directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "CODEX_HOME": str(self._codex_home)},
            )
        except OSError as error:
            raise CodexHookTrustError("could not start Codex app-server") from error
        threading.Thread(target=self._read_stdout, daemon=True).start()
        try:
            result = self._call(
                "initialize",
                {
                    "clientInfo": {"name": "agentic-evo", "version": "1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
        except BaseException:
            self._close()
            raise
        if not isinstance(result, Mapping):
            self._close()
            raise CodexHookTrustError("Codex app-server returned an invalid initialize response")
        return self

    def __exit__(self, *_: object) -> None:
        self._close()

    def list_hooks(self, working_directory: Path) -> list[Mapping[str, Any]]:
        result = self._call("hooks/list", {"cwds": [str(working_directory)]})
        if not isinstance(result, Mapping) or not isinstance(result.get("data"), list):
            raise CodexHookTrustError("Codex app-server returned an invalid hooks list")
        expected_directory = _normalized_path(working_directory)
        for item in result["data"]:
            if not isinstance(item, Mapping):
                continue
            cwd = item.get("cwd")
            hooks = item.get("hooks")
            if (
                isinstance(cwd, str)
                and _normalized_path(cwd) == expected_directory
                and isinstance(hooks, list)
                and all(isinstance(hook, Mapping) for hook in hooks)
            ):
                return list(hooks)
        raise CodexHookTrustError("Codex app-server did not return hooks for this workspace")

    def write_trusted_hashes(
        self,
        edits: Sequence[Mapping[str, str]],
        *,
        config_path: Path,
    ) -> None:
        self._call(
            "config/batchWrite",
            {
                "edits": [dict(edit) for edit in edits],
                "filePath": str(config_path),
                "reloadUserConfig": True,
            },
        )

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._lines.put(None)
            return
        for line in process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _call(self, method: str, params: Mapping[str, Any]) -> Any:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexHookTrustError("Codex app-server is not running")
        request_id = self._next_request_id
        self._next_request_id += 1
        try:
            process.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": dict(params),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            process.stdin.flush()
        except OSError as error:
            raise CodexHookTrustError("could not send the Codex app-server request") from error

        deadline = time.monotonic() + _APP_SERVER_RESPONSE_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexHookTrustError("Codex app-server did not answer in time")
            try:
                line = self._lines.get(timeout=remaining)
            except Empty as error:
                raise CodexHookTrustError("Codex app-server did not answer in time") from error
            if line is None:
                raise CodexHookTrustError("Codex app-server exited before answering")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as error:
                raise CodexHookTrustError("Codex app-server emitted invalid JSON") from error
            if not isinstance(response, Mapping) or response.get("id") != request_id:
                continue
            if "error" in response:
                raise CodexHookTrustError(
                    f"Codex app-server rejected {method}"
                )
            if "result" not in response:
                raise CodexHookTrustError(
                    f"Codex app-server returned no result for {method}"
                )
            return response["result"]

    def _close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def _normalized_path(value: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(value))))


def _require_all_codex_hook_events(targets: Sequence[CodexHookTarget]) -> None:
    event_names = {target.event_name for target in targets}
    if (
        len(targets) != len(REQUIRED_CODEX_HOOK_EVENT_NAMES)
        or event_names != REQUIRED_CODEX_HOOK_EVENT_NAMES
    ):
        raise CodexHookTrustError(
            "Codex did not list the complete required Agentic-Evo hook set"
        )


def _witness_command(home: Path) -> list[str]:
    entrypoint = Path(sys.argv[0]).resolve(strict=False)
    if entrypoint.suffix.lower() == ".pyz":
        return [sys.executable, str(entrypoint), "serve", "--dev-home", str(home)]
    return [sys.executable, "-m", "agentic_evo.cli", "serve", "--dev-home", str(home)]


def _detached_creation_flags() -> int:
    if os.name != "nt":
        return 0
    return (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.CREATE_NO_WINDOW
    )


def ensure_witness(home: Path) -> dict[str, Any]:
    """Return Surface status, starting this application's Witness if needed."""

    home = Path(home).resolve(strict=False)
    client = SurfaceClient(home)
    try:
        return client.status()
    except ServiceNotRunningError:
        pass

    log_path = home / "logs" / "witness-startup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                _witness_command(home),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=_detached_creation_flags(),
                start_new_session=os.name != "nt",
            )
    except OSError as error:
        raise RuntimeError("could not start the Agentic-Evo Witness") from error

    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            return client.status()
        except ServiceNotRunningError:
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"Agentic-Evo Witness exited with status {return_code}; "
                    f"see {log_path}"
                )
            time.sleep(_STARTUP_POLL_SECONDS)
    raise RuntimeError(
        f"Agentic-Evo Witness did not become available; see {log_path}"
    )
