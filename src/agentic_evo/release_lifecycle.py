"""User-level release installation and lifecycle management.

This module owns files, launchers, and user-level startup registration only.
It deliberately never opens trusted state: all runtime management travels through
the public Agentic-Evo CLI protocol.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import tempfile
from typing import Any
import zipfile


INSTALL_SCHEMA = "agentic-evo.release-install.v1"
RECEIPT_SCHEMA = "agentic-evo.release-lifecycle.v1"
DATA_SCHEMA = "agentic-evo.release-data.v1"
_MANIFEST_NAME = "install-manifest.json"
_DATA_MARKER_NAME = ".agentic-evo-release-data.json"
_PYZ_NAME = "agentic-evo.pyz"
_WINDOWS_LAUNCHER = "agentic-evo.cmd"
_UNIX_LAUNCHER = "agentic-evo"
_WINDOWS_CONSOLE_LAUNCHER = "Evo Codex.cmd"
_UNIX_CONSOLE_LAUNCHER = "evo-codex"
_WINDOWS_LINGTAI_LAUNCHER = "Evo LingTai.cmd"
_UNIX_LINGTAI_LAUNCHER = "evo-lingtai"
_WINDOWS_RECOVERY_LAUNCHER = "agentic-evo-recover.vbs"
_RUNTIME_TIMEOUT_SECONDS = 30
_MANAGED_RUNTIME_COMMANDS = ("status", "on", "off", "recover", "shutdown")


class ReleaseLifecycleError(RuntimeError):
    """The requested release lifecycle operation cannot safely proceed."""


@dataclass(frozen=True)
class InstallLayout:
    """Target-specific inputs supplied by the CLI installer.

    ``python_executable`` is verified while installing and written only into the
    target machine's launcher.  It is never a source-tree or release-package
    constant, so a new installation discovers and records its own interpreter.
    """

    program_dir: Path
    data_dir: Path
    runtime_home: Path
    release_artifact: Path
    python_executable: Path
    version: str
    lingtai_enabled: bool = False


@dataclass(frozen=True)
class _ProtocolResult:
    returncode: int | None
    response: dict[str, Any] | None
    error_code: str | None


def install(layout: InstallLayout) -> dict[str, Any]:
    """Install one versioned user-level release and request runtime recovery.

    The CLI must prepare the existing or new runtime identity before calling
    this function.  This module never creates a Root or changes trusted state.
    """

    validate_install_layout(layout)
    layout = _validate_layout(layout)

    stage: Path | None = None
    installed = False
    try:
        layout.program_dir.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(
            tempfile.mkdtemp(
                prefix=".agentic-evo-install-", dir=layout.program_dir.parent
            )
        )
        _write_release_payload(layout, stage)
        os.replace(stage, layout.program_dir)
        stage = None
        installed = True
        _register_autostart(layout)
        layout.data_dir.mkdir(parents=True, exist_ok=True)
        _write_data_marker(layout)
    except OSError:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        if installed:
            _remove_program_dir(layout)
        _record_error(layout, "install", "filesystem_or_startup_registration_error")
        return _failure_receipt(layout, "install", "installation_failed")

    result = _call_runtime_cli(layout, "recover", "--restart")
    if result.returncode != 0:
        _record_error(layout, "install", result.error_code or "runtime_recovery_failed")
        return _failure_receipt(
            layout,
            "install",
            result.error_code or "runtime_recovery_failed",
            installed=True,
            runtime=_runtime_receipt(result),
        )
    return _success_receipt(
        layout,
        "install",
        installed=True,
        runtime=_runtime_receipt(result),
    )


def upgrade(layout: InstallLayout) -> dict[str, Any]:
    """Replace release program files while retaining the existing identity/data."""

    layout = _validate_layout(layout)
    manifest = _load_manifest(layout.program_dir)
    _require_same_identity(layout, manifest)

    # Run the installed release's shutdown before replacing its pyz.  The CLI
    # does not return success until its existing Witness has stopped, so a
    # successful response makes the subsequent replacement safe from lazy
    # imports by the old process.
    shutdown_result = _call_runtime_cli(layout, "shutdown")
    if shutdown_result.returncode != 0 and not _proves_runtime_not_running(
        shutdown_result
    ):
        _record_error(
            layout, "upgrade", shutdown_result.error_code or "runtime_shutdown_failed"
        )
        return _failure_receipt(
            layout,
            "upgrade",
            shutdown_result.error_code or "runtime_shutdown_failed",
            installed=True,
            runtime={"shutdown": _runtime_receipt(shutdown_result)},
        )

    stage: Path | None = None
    try:
        stage = Path(
            tempfile.mkdtemp(
                prefix=".agentic-evo-upgrade-", dir=layout.program_dir.parent
            )
        )
        _write_release_payload(layout, stage)
        for name in _payload_names(layout):
            _replace_file(stage / name, layout.program_dir / name)
        if not layout.lingtai_enabled:
            stale_lingtai = layout.program_dir / _lingtai_launcher_name()
            if stale_lingtai.exists():
                stale_lingtai.unlink()
        _register_autostart(layout)
    except OSError:
        _record_error(layout, "upgrade", "filesystem_or_startup_registration_error")
        return _failure_receipt(layout, "upgrade", "upgrade_failed", installed=True)
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)

    result = _call_runtime_cli(layout, "recover", "--restart")
    if result.returncode != 0:
        _record_error(layout, "upgrade", result.error_code or "runtime_recovery_failed")
        return _failure_receipt(
            layout,
            "upgrade",
            result.error_code or "runtime_recovery_failed",
            installed=True,
            runtime=_runtime_receipt(result),
        )
    return _success_receipt(
        layout,
        "upgrade",
        installed=True,
        runtime=_runtime_receipt(result),
    )


def uninstall(
    layout: InstallLayout, *, preserve_data: bool = True
) -> dict[str, Any]:
    """Remove program files/startup; retain migratable identity data by default.

    ``preserve_data=False`` is the explicit destructive operation.  It removes
    both the configured data directory and an independently located runtime
    home after public ``off`` and ``shutdown`` commands have stopped it.
    """

    layout = _validate_layout(layout)
    if not (layout.program_dir / _MANIFEST_NAME).is_file():
        return _success_receipt(
            layout,
            "uninstall",
            installed=False,
            data_preserved=preserve_data,
        )
    manifest = _load_manifest(layout.program_dir)
    _require_same_identity(layout, manifest)
    if not preserve_data:
        _require_owned_data_dir_for_purge(layout)

    off_result = _call_runtime_cli(layout, "off")
    status_result: _ProtocolResult | None = None
    if off_result.returncode != 0:
        if off_result.returncode != 4:
            _record_error(layout, "uninstall", off_result.error_code or "runtime_off_failed")
            return _failure_receipt(
                layout,
                "uninstall",
                off_result.error_code or "runtime_off_failed",
                installed=True,
                runtime={"off": _runtime_receipt(off_result)},
            )
        status_result = _call_runtime_cli(layout, "status")
        if not _proves_runtime_not_running(status_result):
            _record_error(layout, "uninstall", "runtime_off_unconfirmed")
            return _failure_receipt(
                layout,
                "uninstall",
                "runtime_off_unconfirmed",
                installed=True,
                runtime={
                    "off": _runtime_receipt(off_result),
                    "status": _runtime_receipt(status_result),
                },
            )

    shutdown_result = _call_runtime_cli(layout, "shutdown")
    if shutdown_result.returncode == 4:
        status_result = _call_runtime_cli(layout, "status")
        if not _proves_runtime_not_running(status_result):
            _record_error(layout, "uninstall", "runtime_shutdown_unconfirmed")
            return _failure_receipt(
                layout,
                "uninstall",
                "runtime_shutdown_unconfirmed",
                installed=True,
                runtime={
                    "off": _runtime_receipt(off_result),
                    "shutdown": _runtime_receipt(shutdown_result),
                    "status": _runtime_receipt(status_result),
                },
            )
    elif shutdown_result.returncode != 0:
        _record_error(
            layout, "uninstall", shutdown_result.error_code or "runtime_shutdown_failed"
        )
        return _failure_receipt(
            layout,
            "uninstall",
            shutdown_result.error_code or "runtime_shutdown_failed",
            installed=True,
            runtime={
                "off": _runtime_receipt(off_result),
                "shutdown": _runtime_receipt(shutdown_result),
            },
        )

    runtime = {
        "off": _runtime_receipt(off_result),
        "shutdown": _runtime_receipt(shutdown_result),
    }
    if status_result is not None:
        runtime["status"] = _runtime_receipt(status_result)

    try:
        _remove_autostart(layout)
        _remove_program_dir(layout)
        if not preserve_data:
            for target in _purge_targets(layout):
                if target.exists():
                    _remove_tree(target)
    except OSError:
        _record_error(layout, "uninstall", "uninstall_filesystem_error")
        return _failure_receipt(
            layout,
            "uninstall",
            "uninstall_failed",
            installed=layout.program_dir.exists(),
            data_preserved=preserve_data,
            runtime=runtime,
        )

    return _success_receipt(
        layout,
        "uninstall",
        installed=False,
        data_preserved=preserve_data,
        runtime=runtime,
    )


def installed_status(layout: InstallLayout) -> dict[str, Any]:
    """Return installed state plus public-protocol runtime status when present."""

    layout = _validate_layout(layout)
    manifest_path = layout.program_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        return _success_receipt(layout, "status", installed=False)
    manifest = _load_manifest(layout.program_dir)
    _require_same_identity(layout, manifest)
    missing = [
        name for name in _payload_names(layout) if not (layout.program_dir / name).is_file()
    ]
    if missing:
        _record_error(layout, "status", "installed_payload_missing")
        return _failure_receipt(
            layout,
            "status",
            "installed_payload_missing",
            installed=True,
            missing_files=missing,
        )

    result = _call_runtime_cli(layout, "status")
    if result.returncode != 0:
        _record_error(layout, "status", result.error_code or "runtime_status_failed")
        return _failure_receipt(
            layout,
            "status",
            result.error_code or "runtime_status_failed",
            installed=True,
            runtime=_runtime_receipt(result),
        )
    return _success_receipt(
        layout,
        "status",
        installed=True,
        runtime=_runtime_receipt(result),
    )


def load_installed_layout(
    program_dir: Path,
    *,
    release_artifact: Path,
    python_executable: Path,
) -> InstallLayout:
    """Load the installed identity layout and apply this operation's release inputs.

    This is the sole manifest-to-layout boundary for CLI upgrade and uninstall
    paths.  It refuses a moved or malformed installation rather than guessing a
    replacement Root or data location.
    """

    resolved_program_dir = _absolute_path(program_dir, "program_dir")
    _require_safe_managed_path(resolved_program_dir, "program_dir")
    manifest = _load_manifest(resolved_program_dir)
    values: dict[str, Path] = {}
    for field in ("program_dir", "data_dir", "runtime_home"):
        value = manifest.get(field)
        if not isinstance(value, str):
            raise ReleaseLifecycleError("installed release manifest is invalid")
        values[field] = _absolute_path(Path(value), field)
    if values["program_dir"] != resolved_program_dir:
        raise ReleaseLifecycleError("installed release manifest does not own program_dir")
    lingtai_enabled = manifest.get("lingtai_enabled")
    if not isinstance(lingtai_enabled, bool):
        raise ReleaseLifecycleError("installed release manifest is invalid")
    artifact = _absolute_path(Path(release_artifact), "release_artifact")
    return _validate_layout(
        InstallLayout(
            program_dir=values["program_dir"],
            data_dir=values["data_dir"],
            runtime_home=values["runtime_home"],
            release_artifact=artifact,
            python_executable=Path(python_executable),
            version=_release_artifact_version(artifact),
            lingtai_enabled=lingtai_enabled,
        )
    )


def _release_artifact_version(artifact: Path) -> str:
    """Read one static VERSION assignment from a release zipapp.

    This deliberately parses package text rather than importing or executing a
    candidate artifact supplied to an upgrade command.
    """

    try:
        with zipfile.ZipFile(artifact) as bundle:
            text = bundle.read("agentic_evo/version.py").decode("utf-8")
        tree = ast.parse(text, filename="agentic_evo/version.py")
    except (OSError, KeyError, UnicodeError, SyntaxError, zipfile.BadZipFile) as error:
        raise ReleaseLifecycleError(
            "release_artifact version module could not be read"
        ) from error
    versions = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "VERSION"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(versions) != 1:
        raise ReleaseLifecycleError(
            "release_artifact version module must define one string VERSION"
        )
    return versions[0]


def validate_install_layout(layout: InstallLayout) -> InstallLayout:
    """Validate a new install target before CLI identity/configuration writes.

    A runtime home may be absent at this point: the public CLI creates or
    verifies that identity after this boundary.  The data directory may only
    be absent, empty, or already marked as owned by this exact runtime home.
    """

    normalized = _normalize_layout(layout, require_runtime_home=False)
    if normalized.program_dir.exists():
        raise ReleaseLifecycleError(
            "program directory already exists; use upgrade for an installed release"
        )
    _validate_data_dir_for_install(normalized)
    return normalized


def _validate_layout(layout: InstallLayout) -> InstallLayout:
    return _normalize_layout(layout, require_runtime_home=True)


def _normalize_layout(
    layout: InstallLayout, *, require_runtime_home: bool
) -> InstallLayout:
    if not isinstance(layout, InstallLayout):
        raise TypeError("layout must be an InstallLayout")
    version = layout.version.strip()
    if not version or any(character not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._+-" for character in version):
        raise ReleaseLifecycleError("version must contain only release-safe characters")
    normalized = InstallLayout(
        program_dir=_absolute_path(layout.program_dir, "program_dir"),
        data_dir=_absolute_path(layout.data_dir, "data_dir"),
        runtime_home=_absolute_path(layout.runtime_home, "runtime_home"),
        release_artifact=_absolute_path(layout.release_artifact, "release_artifact"),
        python_executable=_absolute_path(layout.python_executable, "python_executable"),
        version=version,
        lingtai_enabled=layout.lingtai_enabled,
    )
    if not isinstance(normalized.lingtai_enabled, bool):
        raise ReleaseLifecycleError("lingtai_enabled must be a boolean")
    for path, label in (
        (normalized.program_dir, "program_dir"),
        (normalized.data_dir, "data_dir"),
        (normalized.runtime_home, "runtime_home"),
    ):
        _require_safe_managed_path(path, label)
    if _overlap(normalized.program_dir, normalized.data_dir):
        raise ReleaseLifecycleError("program_dir and data_dir must be separate")
    if _overlap(normalized.program_dir, normalized.runtime_home):
        raise ReleaseLifecycleError("program_dir must not contain the runtime home")
    if _overlap(normalized.data_dir, normalized.runtime_home):
        raise ReleaseLifecycleError("data_dir must not overlap the runtime home")
    if require_runtime_home and not normalized.runtime_home.is_dir():
        raise ReleaseLifecycleError("runtime_home must be an existing runtime directory")
    if not normalized.release_artifact.is_file() or normalized.release_artifact.suffix != ".pyz":
        raise ReleaseLifecycleError("release_artifact must be an existing .pyz file")
    if not normalized.python_executable.is_file():
        raise ReleaseLifecycleError("python_executable must be an existing file")
    return normalized


def _absolute_path(value: Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ReleaseLifecycleError(f"{label} must be absolute")
    return path.resolve(strict=False)


def _require_safe_managed_path(path: Path, label: str) -> None:
    if path == Path(path.anchor) or len(path.parts) < 3:
        raise ReleaseLifecycleError(f"{label} is too broad for lifecycle management")


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _data_marker_path(layout: InstallLayout) -> Path:
    return layout.data_dir / _DATA_MARKER_NAME


def _data_dir_owned_by(layout: InstallLayout) -> bool:
    path = _data_marker_path(layout)
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(marker, dict) or marker.get("schema") != DATA_SCHEMA:
        return False
    for field, expected in (
        ("data_dir", layout.data_dir),
        ("runtime_home", layout.runtime_home),
    ):
        value = marker.get(field)
        if not isinstance(value, str):
            return False
        try:
            if _absolute_path(Path(value), field) != expected:
                return False
        except ReleaseLifecycleError:
            return False
    return True


def _validate_data_dir_for_install(layout: InstallLayout) -> None:
    if not layout.data_dir.exists():
        return
    if not layout.data_dir.is_dir():
        raise ReleaseLifecycleError("data_dir must be a directory")
    if _data_dir_owned_by(layout):
        return
    try:
        has_contents = next(layout.data_dir.iterdir(), None) is not None
    except OSError as error:
        raise ReleaseLifecycleError("data_dir could not be inspected") from error
    if has_contents:
        raise ReleaseLifecycleError(
            "data_dir must be empty or owned by this Agentic-Evo release"
        )


def _require_owned_data_dir_for_purge(layout: InstallLayout) -> None:
    if layout.data_dir.exists() and not _data_dir_owned_by(layout):
        raise ReleaseLifecycleError(
            "data_dir is not owned by this Agentic-Evo release and cannot be purged"
        )


def _write_data_marker(layout: InstallLayout) -> None:
    _write_text(
        _data_marker_path(layout),
        json.dumps(
            {
                "schema": DATA_SCHEMA,
                "data_dir": str(layout.data_dir),
                "runtime_home": str(layout.runtime_home),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _payload_names(layout: InstallLayout) -> tuple[str, ...]:
    payload = [
        _PYZ_NAME,
        _launcher_name(),
        _console_launcher_name(),
        _MANIFEST_NAME,
    ]
    if layout.lingtai_enabled:
        payload.append(_lingtai_launcher_name())
    if os.name == "nt":
        payload.append(_WINDOWS_RECOVERY_LAUNCHER)
    return tuple(payload)


def _launcher_name() -> str:
    return _WINDOWS_LAUNCHER if os.name == "nt" else _UNIX_LAUNCHER


def _console_launcher_name() -> str:
    return _WINDOWS_CONSOLE_LAUNCHER if os.name == "nt" else _UNIX_CONSOLE_LAUNCHER


def _lingtai_launcher_name() -> str:
    return _WINDOWS_LINGTAI_LAUNCHER if os.name == "nt" else _UNIX_LINGTAI_LAUNCHER


def _write_release_payload(layout: InstallLayout, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    _copy_file(layout.release_artifact, destination / _PYZ_NAME)
    _write_text(
        destination / _launcher_name(),
        _runtime_launcher_text(layout),
        executable=os.name != "nt",
    )
    _write_text(
        destination / _console_launcher_name(),
        _console_launcher_text(layout),
        executable=os.name != "nt",
    )
    if layout.lingtai_enabled:
        _write_text(
            destination / _lingtai_launcher_name(),
            _lingtai_launcher_text(layout),
            executable=os.name != "nt",
        )
    if os.name == "nt":
        _write_text(
            destination / _WINDOWS_RECOVERY_LAUNCHER,
            _windows_recovery_launcher_text(layout),
        )
    _write_text(
        destination / _MANIFEST_NAME,
        json.dumps(_manifest(layout), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _manifest(layout: InstallLayout) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": INSTALL_SCHEMA,
        "version": layout.version,
        "program_dir": str(layout.program_dir),
        "data_dir": str(layout.data_dir),
        "runtime_home": str(layout.runtime_home),
        "python_executable": str(layout.python_executable),
        "artifact": _PYZ_NAME,
        "launcher": _launcher_name(),
        "console_launcher": _console_launcher_name(),
        "lingtai_enabled": layout.lingtai_enabled,
        "startup": _autostart_descriptor(layout),
    }
    if layout.lingtai_enabled:
        manifest["lingtai_launcher"] = _lingtai_launcher_name()
    return manifest


def _runtime_launcher_text(layout: InstallLayout) -> str:
    if os.name == "nt":
        command = f'{_quote_windows(layout.python_executable)} "%~dp0{_PYZ_NAME}" %*'
        managed_command = command + " --home " + _quote_windows(layout.runtime_home)
        return "\r\n".join(
            (
                "@echo off",
                'if "%~1"=="uninstall" goto uninstall',
                "chcp 65001 >nul",
                'set "PYTHONUTF8=1"',
                "setlocal DisableDelayedExpansion",
                "call :has_explicit_managed_home %*",
                'if defined EVO_EXPLICIT_MANAGED_HOME goto run_explicit',
                *(
                    f'if /I "%~1"=="{name}" goto run_managed'
                    for name in _MANAGED_RUNTIME_COMMANDS
                ),
                ":run_explicit",
                command,
                'set "EVO_LAUNCHER_RC=%errorlevel%"',
                "endlocal & exit /b %EVO_LAUNCHER_RC%",
                ":run_managed",
                managed_command,
                'set "EVO_LAUNCHER_RC=%errorlevel%"',
                "endlocal & exit /b %EVO_LAUNCHER_RC%",
                ":uninstall",
                # Uninstall removes this launcher. The parsed GOTO returns to
                # the caller without rereading it; CALL captures child status.
                "chcp 65001 >nul & set \"PYTHONUTF8=1\" & "
                + command
                + " & (goto) 2>nul & call cmd.exe /d /c exit /b %%errorlevel%%",
                ":has_explicit_managed_home",
                'set "EVO_EXPLICIT_MANAGED_HOME="',
                ":scan_managed_home",
                'if "%~1"=="" exit /b 0',
                'set "EVO_LAUNCHER_ARGUMENT=%~1"',
                'if /I "%EVO_LAUNCHER_ARGUMENT%"=="--home" set "EVO_EXPLICIT_MANAGED_HOME=1"',
                'if /I "%EVO_LAUNCHER_ARGUMENT%"=="--dev-home" set "EVO_EXPLICIT_MANAGED_HOME=1"',
                'if /I "%EVO_LAUNCHER_ARGUMENT:~0,7%"=="--home=" set "EVO_EXPLICIT_MANAGED_HOME=1"',
                'if /I "%EVO_LAUNCHER_ARGUMENT:~0,11%"=="--dev-home=" set "EVO_EXPLICIT_MANAGED_HOME=1"',
                'if defined EVO_EXPLICIT_MANAGED_HOME exit /b 0',
                "shift",
                "goto scan_managed_home",
                "",
            )
        )
    executable = shlex.quote(str(layout.python_executable))
    artifact = '"$(dirname "$0")/' + _PYZ_NAME + '"'
    managed_command = " | ".join(_MANAGED_RUNTIME_COMMANDS)
    return "\n".join(
        (
            "#!/bin/sh",
            'case "$1" in',
            f"  {managed_command})",
            '    for EVO_LAUNCHER_ARGUMENT in "$@"; do',
            '      case "$EVO_LAUNCHER_ARGUMENT" in',
            '        --home|--home=*|--dev-home|--dev-home=*)',
            f'          exec {executable} {artifact} "$@"',
            "          ;;",
            "      esac",
            "    done",
            f"    exec {executable} {artifact} \"$@\" --home {shlex.quote(str(layout.runtime_home))}",
            "    ;;",
            "esac",
            f'exec {executable} {artifact} "$@"',
            "",
        )
    )


def _console_launcher_text(layout: InstallLayout) -> str:
    return _daily_launcher_text(
        layout,
        label="Codex",
        command="console",
        fixed_arguments=("--surface", "codex"),
    )


def _lingtai_launcher_text(layout: InstallLayout) -> str:
    return _daily_launcher_text(
        layout,
        label="LingTai",
        command="lingtai",
        fixed_arguments=(),
    )


def _daily_launcher_text(
    layout: InstallLayout,
    *,
    label: str,
    command: str,
    fixed_arguments: tuple[str, ...],
) -> str:
    if os.name == "nt":
        invocation = " ".join(
            (
                f'call "%~dp0{_launcher_name()}"',
                command,
                "--home",
                _quote_windows(layout.runtime_home),
                *fixed_arguments,
                '--cwd "%EVO_PROJECT_DIR%"',
                "%*",
            )
        )
        return "\r\n".join(
            (
                "@echo off",
                "for %%I in (\"%~dp0.\") do set \"EVO_PROGRAM_DIR=%%~fI\"",
                "set \"EVO_PROJECT_DIR=%CD%\"",
                "if /I \"%EVO_PROJECT_DIR%\"==\"%EVO_PROGRAM_DIR%\" goto choose_project",
                "if not exist \"%EVO_PROJECT_DIR%\\.\" goto choose_project",
                "goto run_console",
                ":choose_project",
                "set /p \"EVO_PROJECT_DIR=Enter an existing project directory: \"",
                "if not exist \"%EVO_PROJECT_DIR%\\.\" exit /b 2",
                ":run_console",
                f"echo Agentic-Evo {label} project: %EVO_PROJECT_DIR%",
                invocation,
                "exit /b %errorlevel%",
                "",
            )
        )
    invocation = " ".join(
        (
            f'"$EVO_PROGRAM_DIR/{_launcher_name()}"',
            command,
            "--home",
            shlex.quote(str(layout.runtime_home)),
            *(shlex.quote(argument) for argument in fixed_arguments),
            '--cwd "$EVO_PROJECT_DIR"',
            '"$@"',
        )
    )
    return "\n".join(
        (
            "#!/bin/sh",
            'EVO_PROGRAM_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"',
            'EVO_PROJECT_DIR="$(pwd -P)"',
            'if [ "$EVO_PROJECT_DIR" = "$EVO_PROGRAM_DIR" ] || [ ! -d "$EVO_PROJECT_DIR" ]; then',
            '  printf "%s" "Enter an existing project directory: " >&2',
            '  IFS= read -r EVO_PROJECT_DIR',
            '  [ -d "$EVO_PROJECT_DIR" ] || exit 2',
            "fi",
            f'printf "%s\\n" "Agentic-Evo {label} project: $EVO_PROJECT_DIR"',
            "exec " + invocation,
            "",
        )
    )


def _quote_windows(value: Path | str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _windows_recovery_launcher_text(layout: InstallLayout) -> str:
    quoted_command = " ".join(
        _quote_windows(argument) for argument in _runtime_program_arguments(layout, "recover")
    )
    return "\r\n".join(
        (
            'Set shell = CreateObject("WScript.Shell")',
            'shell.Run "' + quoted_command.replace('"', '""') + '", 0, False',
            "",
        )
    )


def _copy_file(source: Path, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}-", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _replace_file(source: Path, destination: Path) -> None:
    _copy_file(source, destination)


def _write_text(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        if executable:
            os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_manifest(program_dir: Path) -> dict[str, Any]:
    path = program_dir / _MANIFEST_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseLifecycleError("installed release manifest could not be read") from error
    if not isinstance(value, dict) or value.get("schema") != INSTALL_SCHEMA:
        raise ReleaseLifecycleError("installed release manifest is invalid")
    return value


def _require_same_identity(layout: InstallLayout, manifest: dict[str, Any]) -> None:
    for field, expected in (
        ("program_dir", layout.program_dir),
        ("data_dir", layout.data_dir),
        ("runtime_home", layout.runtime_home),
    ):
        value = manifest.get(field)
        if not isinstance(value, str) or _absolute_path(Path(value), field) != expected:
            raise ReleaseLifecycleError(
                "upgrade/uninstall must retain the installed runtime identity and data"
            )


def _proves_runtime_not_running(result: _ProtocolResult) -> bool:
    return result.returncode == 4 and result.error_code == "service_not_running"


def _call_runtime_cli(
    layout: InstallLayout, command: str, *extra_arguments: str
) -> _ProtocolResult:
    argv = [
        str(layout.python_executable),
        str(layout.program_dir / _PYZ_NAME),
        command,
        "--home",
        str(layout.runtime_home),
        *extra_arguments,
    ]
    try:
        completed = subprocess.run(
            argv,
            cwd=layout.program_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RUNTIME_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _ProtocolResult(None, None, "runtime_command_timed_out")
    except OSError:
        return _ProtocolResult(None, None, "runtime_command_unavailable")

    response = _json_mapping(
        completed.stdout if completed.returncode == 0 else completed.stderr
    )
    if completed.returncode == 0:
        return _ProtocolResult(0, response, None)
    error_code = _protocol_error_code(response)
    if completed.returncode == 4:
        return _ProtocolResult(4, response, error_code or "runtime_unavailable")
    return _ProtocolResult(
        completed.returncode,
        response,
        error_code or "runtime_command_failed",
    )


def _json_mapping(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _protocol_error_code(response: dict[str, Any] | None) -> str | None:
    if response is None:
        return None
    error = response.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) and code else None


def _runtime_receipt(result: _ProtocolResult) -> dict[str, Any]:
    value: dict[str, Any] = {
        "attempted": True,
        "exit_code": result.returncode,
    }
    if result.response is not None:
        value["status"] = result.response
    return value


def _autostart_descriptor(layout: InstallLayout) -> dict[str, str]:
    if os.name == "nt":
        return {
            "scope": "user",
            "kind": "windows_registry_run",
            "location": r"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
            "value_name": "AgenticEvo",
        }
    if sys_platform() == "darwin":
        return {
            "scope": "user",
            "kind": "launch_agent",
            "location": str(_macos_launch_agent_path()),
        }
    return {
        "scope": "user",
        "kind": "xdg_autostart",
        "location": str(_linux_autostart_path()),
    }


def sys_platform() -> str:
    """A small seam for platform-specific automated tests."""

    import sys

    return sys.platform


def _register_autostart(layout: InstallLayout) -> None:
    if os.name == "nt":
        import winreg

        command = " ".join(
            (
                "wscript.exe",
                _quote_windows(layout.program_dir / _WINDOWS_RECOVERY_LAUNCHER),
            )
        )
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "AgenticEvo", 0, winreg.REG_SZ, command)
        return
    if sys_platform() == "darwin":
        _write_bytes(
            _macos_launch_agent_path(),
            plistlib.dumps(
                {
                    "Label": "com.agentic-evo.runtime",
                    "ProgramArguments": _runtime_program_arguments(layout, "recover"),
                    "RunAtLoad": True,
                    "KeepAlive": False,
                },
                fmt=plistlib.FMT_XML,
            ),
        )
        return
    _write_text(
        _linux_autostart_path(),
        "\n".join(
            (
                "[Desktop Entry]",
                "Type=Application",
                "Name=Agentic-Evo Runtime",
                "Exec=" + _desktop_exec(_runtime_program_arguments(layout, "recover")),
                "X-GNOME-Autostart-enabled=true",
                "",
            )
        ),
    )


def _remove_autostart(layout: InstallLayout) -> None:
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, "AgenticEvo")
        except FileNotFoundError:
            pass
        return
    path = _macos_launch_agent_path() if sys_platform() == "darwin" else _linux_autostart_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _runtime_program_arguments(layout: InstallLayout, command: str) -> list[str]:
    return [
        str(layout.python_executable),
        str(layout.program_dir / _PYZ_NAME),
        command,
        "--home",
        str(layout.runtime_home),
    ]


def _macos_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.agentic-evo.runtime.plist"


def _linux_autostart_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "autostart" / "agentic-evo.desktop"


def _desktop_exec(arguments: list[str]) -> str:
    return " ".join(shlex.quote(argument) for argument in arguments)


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_program_dir(layout: InstallLayout) -> None:
    _remove_tree(layout.program_dir)


def _remove_tree(path: Path) -> None:
    _require_safe_managed_path(path, "removal target")
    shutil.rmtree(path)


def _purge_targets(layout: InstallLayout) -> tuple[Path, ...]:
    targets: list[Path] = []
    for candidate in sorted(
        {layout.data_dir, layout.runtime_home}, key=lambda path: len(path.parts)
    ):
        if not any(existing == candidate or existing in candidate.parents for existing in targets):
            targets.append(candidate)
    return tuple(targets)


def _record_error(layout: InstallLayout, operation: str, code: str) -> None:
    """Record technical metadata only; never persist CLI output or arguments."""

    try:
        log_dir = layout.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "code": code,
        }
        with (log_dir / "release-lifecycle.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def _success_receipt(
    layout: InstallLayout, operation: str, **details: Any
) -> dict[str, Any]:
    return _receipt(layout, operation, ok=True, **details)


def _failure_receipt(
    layout: InstallLayout, operation: str, code: str, **details: Any
) -> dict[str, Any]:
    return _receipt(
        layout,
        operation,
        ok=False,
        error={"code": code},
        **details,
    )


def _receipt(
    layout: InstallLayout, operation: str, *, ok: bool, **details: Any
) -> dict[str, Any]:
    daily_launchers = {
        "codex": str(layout.program_dir / _console_launcher_name()),
    }
    if layout.lingtai_enabled:
        daily_launchers["lingtai"] = str(
            layout.program_dir / _lingtai_launcher_name()
        )
    return {
        "schema": RECEIPT_SCHEMA,
        "ok": ok,
        "operation": operation,
        "version": layout.version,
        "program_dir": str(layout.program_dir),
        "data_dir": str(layout.data_dir),
        "runtime_home": str(layout.runtime_home),
        "daily_launchers": daily_launchers,
        **details,
    }
