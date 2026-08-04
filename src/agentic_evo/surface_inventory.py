from __future__ import annotations

from pathlib import Path
import shutil
import sys
from typing import Callable


SURFACE_INVENTORY_SCHEMA = "agentic-evo.execution-surface-inventory.v1"

KNOWN_SURFACE_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("codex", ("codex",)),
    ("claude_code", ("claude", "claude-code")),
    ("aider", ("aider",)),
    ("goose", ("goose",)),
    ("opencode", ("opencode",)),
    ("gemini_cli", ("gemini",)),
    ("qwen_code", ("qwen-code", "qwen")),
)


def detect_execution_surfaces(
    *,
    which: Callable[[str], str | None] = shutil.which,
    platform: str = sys.platform,
) -> dict[str, object]:
    surfaces: list[dict[str, object]] = []
    for surface, commands in KNOWN_SURFACE_COMMANDS:
        hits = []
        for command in commands:
            path = which(command)
            if path is None:
                continue
            hits.append(
                {
                    "command": command,
                    "path": str(Path(path)),
                }
            )
        if hits:
            surfaces.append(
                {
                    "surface": surface,
                    "reachable_commands": hits,
                }
            )
    return {
        "schema": SURFACE_INVENTORY_SCHEMA,
        "platform": platform,
        "surface_count": len(surfaces),
        "surfaces": surfaces,
        "claim_ceiling": (
            "reachable commands are execution-surface candidates only; "
            "they do not prove natural-task evidence, lifecycle integration, "
            "or a second real coding-agent"
        ),
    }
