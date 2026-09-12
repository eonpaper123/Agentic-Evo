from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping


_CODEX_POOL_PROVIDERS = frozenset({"codex", "codex-pool", "codex_pool"})


class LingTaiRunError(RuntimeError):
    """The explicit LingTai surface could not complete its native run."""


@dataclass(frozen=True)
class LingTaiRunResult:
    native_task_id: str
    native_run_id: str
    success: bool
    readable_final: str


def run_lingtai_task(
    *,
    home: Path,
    prompt: str,
    working_dir: Path,
    lingtai_python: Path,
    preset: str | None = None,
) -> LingTaiRunResult:
    """Run one real LingTai Agent task through Evo's owned native entry.

    The target project stays outside the managed LingTai station.  The native
    entry supplies its absolute path to LingTai's normal file and shell tools;
    it never creates LingTai control files in that project.
    """

    resolved_home = _required_directory(home, "home")
    resolved_working_dir = _required_directory(working_dir, "working_dir")
    resolved_python = _required_file(lingtai_python, "lingtai_python")
    resolved_preset = _required_preset(preset)
    if not isinstance(prompt, str) or not prompt.strip():
        raise LingTaiRunError("prompt must be a non-empty string")

    request = {
        "home": str(resolved_home),
        "prompt": prompt,
        "working_dir": str(resolved_working_dir),
        "preset": str(resolved_preset),
    }
    environment = os.environ.copy()
    package_root = Path(__file__).resolve().parents[2]
    prior_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(package_root)
        if not prior_pythonpath
        else os.pathsep.join((str(package_root), prior_pythonpath))
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    tui_dir = _codex_tui_dir_from_preset(resolved_preset)
    if tui_dir is not None:
        environment["LINGTAI_TUI_DIR"] = str(tui_dir)

    entry_resource = resources.files("agentic_evo").joinpath(
        "native", "agentic-evo-lingtai.py"
    )
    with resources.as_file(entry_resource) as entry:
        completed = subprocess.run(
            [str(resolved_python), str(entry)],
            input=json.dumps(request, ensure_ascii=True),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            cwd=str(resolved_working_dir),
            env=environment,
            check=False,
        )

    if completed.returncode != 0:
        raise LingTaiRunError(
            f"LingTai native entry failed with exit code {completed.returncode}"
        )
    return _parse_native_result(completed.stdout)


def _required_directory(value: Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise LingTaiRunError(f"{label} must be an existing absolute directory")
    return path.resolve()


def _required_file(value: Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise LingTaiRunError(f"{label} must be an existing absolute file")
    return path.resolve()


def _required_preset(value: str | None) -> Path:
    if not isinstance(value, str) or not value:
        raise LingTaiRunError("an explicit absolute LingTai preset is required")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise LingTaiRunError("preset must be an existing absolute file")
    return path.resolve()


def _codex_tui_dir_from_preset(preset: Path) -> Path | None:
    try:
        document = json.loads(preset.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LingTaiRunError("LingTai preset must be readable UTF-8 JSON") from error
    if not isinstance(document, Mapping):
        raise LingTaiRunError("LingTai preset must contain one JSON object")
    manifest = document.get("manifest")
    if not isinstance(manifest, Mapping):
        raise LingTaiRunError("LingTai preset manifest must be one JSON object")
    llm = manifest.get("llm")
    if not isinstance(llm, Mapping):
        raise LingTaiRunError("LingTai preset manifest.llm must be one JSON object")
    provider = llm.get("provider")
    if not isinstance(provider, str) or not provider:
        raise LingTaiRunError("LingTai preset manifest.llm.provider must be a string")
    if provider.casefold() not in _CODEX_POOL_PROVIDERS:
        return None
    pool_value = llm.get("codex_auth_pool_path")
    if not isinstance(pool_value, str) or not pool_value:
        raise LingTaiRunError("Codex preset requires an absolute auth pool path")
    pool_path = Path(pool_value)
    if not pool_path.is_absolute() or not pool_path.is_file():
        raise LingTaiRunError("Codex preset requires an existing absolute auth pool path")
    return pool_path.resolve().parent


def _parse_native_result(stdout: str) -> LingTaiRunResult:
    try:
        result = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise LingTaiRunError("LingTai native entry returned invalid JSON") from error
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise LingTaiRunError("LingTai native entry returned an invalid result")

    task_id = result.get("native_task_id")
    run_id = result.get("native_run_id")
    success = result.get("success")
    readable_final = result.get("readable_final")
    if (
        not isinstance(task_id, str)
        or not task_id
        or not isinstance(run_id, str)
        or not run_id
        or not isinstance(success, bool)
        or not isinstance(readable_final, str)
    ):
        raise LingTaiRunError("LingTai native entry returned an incomplete result")
    return LingTaiRunResult(
        native_task_id=task_id,
        native_run_id=run_id,
        success=success,
        readable_final=readable_final,
    )
