"""Build a versioned, Python-standard-library Agentic-Evo release archive."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipapp
import zipfile


PACKAGE_SCHEMA = "agentic-evo.release-package.v1"


class ReleaseBuildError(RuntimeError):
    """The source tree cannot be turned into a portable release archive."""


def build_release(*, source: Path, output_dir: Path, version: str) -> Path:
    source = Path(source).resolve(strict=False)
    output_dir = Path(output_dir).resolve(strict=False)
    version = _validate_version(version)
    if not source.is_dir() or not (source / "__main__.py").is_file():
        raise ReleaseBuildError("source must be a package root containing __main__.py")
    if not (source / "agentic_evo").is_dir():
        raise ReleaseBuildError("source must contain the agentic_evo package")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"agentic-evo-{version}.zip"
    if archive.exists():
        raise ReleaseBuildError("release archive already exists")

    stage = Path(tempfile.mkdtemp(prefix=".agentic-evo-release-", dir=output_dir))
    temporary_archive = output_dir / f".{archive.name}.tmp-{os.getpid()}"
    package_name = f"agentic-evo-{version}"
    try:
        staged_source = stage / "source"
        shutil.copytree(
            source,
            staged_source,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        _stamp_packaged_version(staged_source, version)
        package_dir = stage / package_name
        package_dir.mkdir()
        pyz = package_dir / "agentic-evo.pyz"
        zipapp.create_archive(
            staged_source,
            pyz,
            compressed=True,
            filter=lambda path: "__pycache__" not in path.parts
            and path.suffix != ".pyc",
        )
        if _packaged_version(pyz) != version:
            raise ReleaseBuildError("zipapp version does not match requested release")
        _write_text(package_dir / "install.cmd", _windows_installer())
        _write_text(package_dir / "install.sh", _unix_installer(), executable=True)
        _write_text(
            package_dir / "release.json",
            json.dumps(_package_manifest(version), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        _write_text(package_dir / "README.txt", _readme(version))

        with zipfile.ZipFile(
            temporary_archive, "x", compression=zipfile.ZIP_DEFLATED
        ) as bundle:
            for path in sorted(package_dir.iterdir(), key=lambda item: item.name):
                bundle.write(path, arcname=f"{package_name}/{path.name}")
        os.replace(temporary_archive, archive)
    finally:
        if temporary_archive.exists():
            temporary_archive.unlink()
        shutil.rmtree(stage, ignore_errors=True)
    return archive


def _validate_version(value: str) -> str:
    version = value.strip()
    if not version or any(
        character not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._+-"
        for character in version
    ):
        raise ReleaseBuildError("version must contain only release-safe characters")
    return version


def _stamp_packaged_version(source: Path, version: str) -> None:
    path = source / "agentic_evo" / "version.py"
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise ReleaseBuildError("source version module could not be read") from error
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "VERSION"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(assignments) != 1:
        raise ReleaseBuildError("source version module must define one string VERSION")
    assignment = assignments[0]
    lines = text.splitlines(keepends=True)
    lines[assignment.lineno - 1 : assignment.end_lineno] = [f"VERSION = {version!r}\n"]
    path.write_text("".join(lines), encoding="utf-8", newline="")


def _packaged_version(pyz: Path) -> str:
    try:
        with zipfile.ZipFile(pyz) as bundle:
            text = bundle.read("agentic_evo/version.py").decode("utf-8")
        tree = ast.parse(text, filename="agentic_evo/version.py")
    except (OSError, KeyError, UnicodeError, SyntaxError, zipfile.BadZipFile) as error:
        raise ReleaseBuildError("zipapp version module could not be verified") from error
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "VERSION"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise ReleaseBuildError("zipapp version module does not define string VERSION")


def _windows_installer() -> str:
    return "\r\n".join(
        (
            "@echo off",
            'python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"',
            "if errorlevel 1 (",
            "  echo Agentic-Evo requires Python 3.12 or later as python on PATH.",
            "  exit /b 2",
            ")",
            'python "%~dp0agentic-evo.pyz" install --artifact "%~dp0agentic-evo.pyz" %*',
            "exit /b %errorlevel%",
            "",
        )
    )


def _unix_installer() -> str:
    return "\n".join(
        (
            "#!/bin/sh",
            "python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'",
            "if [ $? -ne 0 ]; then",
            '  echo "Agentic-Evo requires Python 3.12 or later as python on PATH." >&2',
            "  exit 2",
            "fi",
            'exec python "$(dirname "$0")/agentic-evo.pyz" install --artifact "$(dirname "$0")/agentic-evo.pyz" "$@"',
            "",
        )
    )


def _package_manifest(version: str) -> dict[str, object]:
    return {
        "schema": PACKAGE_SCHEMA,
        "version": version,
        "artifact": {"path": "agentic-evo.pyz", "format": "zipapp"},
        "installers": {
            "windows": "install.cmd",
            "unix": "install.sh",
        },
        "daily_launchers": {
            "windows": ["Evo Codex.cmd"],
            "unix": ["evo-codex"],
        },
        "optional_daily_launchers": {
            "windows": ["Evo LingTai.cmd"],
            "unix": ["evo-lingtai"],
        },
        "runtime_requirement": "Python >= 3.12",
    }


def _readme(version: str) -> str:
    return "\n".join(
        (
            f"Agentic-Evo {version}",
            "",
            "Extract this archive before installing.",
            "Python 3.12 or later must be available as `python` on PATH.",
            "Windows: run install.cmd --home <runtime-home> --codex-executable <absolute-codex-executable>.",
            "macOS/Linux: run ./install.sh --home <runtime-home> --codex-executable <absolute-codex-executable>.",
            "The optional Evo LingTai harness is enabled only when both --lingtai-python <absolute-lingtai-venv-python> and --lingtai-preset <absolute-existing-preset> are supplied.",
            "Provide both --lingtai-python and --lingtai-preset to enable it; omit both to install the Codex surface alone.",
            "An existing valid runtime home is reused. A new or empty home creates an identity and requires --host-binding; omitting it prompts safely.",
            "Daily entry points: Evo Codex.cmd on Windows and evo-codex on macOS/Linux. Evo LingTai.cmd is added only when enabled (evo-lingtai on macOS/Linux).",
            "",
        )
    )


def _write_text(path: Path, text: str, *, executable: bool = False) -> None:
    path.write_text(text, encoding="utf-8", newline="")
    if executable:
        os.chmod(path, 0o755)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a versioned Agentic-Evo user-level release archive."
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src",
        help="Package root containing __main__.py and agentic_evo (default: repository src).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        archive = build_release(
            source=arguments.source,
            output_dir=arguments.output_dir,
            version=arguments.version,
        )
    except (OSError, ReleaseBuildError, ValueError) as error:
        print(json.dumps({"ok": False, "error": {"code": "release_build_failed", "message": str(error)}}))
        return 6
    print(
        json.dumps(
            {
                "ok": True,
                "schema": PACKAGE_SCHEMA,
                "archive": str(archive),
                "version": arguments.version,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
