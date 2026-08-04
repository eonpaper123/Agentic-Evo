from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from agentic_evo.cli import main
from agentic_evo.surface_inventory import (
    SURFACE_INVENTORY_SCHEMA,
    detect_execution_surfaces,
)


class SurfaceInventoryTests(unittest.TestCase):
    def test_detect_execution_surfaces_reports_reachable_commands(self) -> None:
        hits = {
            "codex": r"C:\tools\codex.exe",
            "claude": r"C:\tools\claude.exe",
        }

        def fake_which(command: str) -> str | None:
            return hits.get(command)

        result = detect_execution_surfaces(which=fake_which, platform="win32")

        self.assertEqual(result["schema"], SURFACE_INVENTORY_SCHEMA)
        self.assertEqual(result["platform"], "win32")
        self.assertEqual(result["surface_count"], 2)
        self.assertEqual(
            result["surfaces"],
            [
                {
                    "surface": "codex",
                    "reachable_commands": [
                        {
                            "command": "codex",
                            "path": r"C:\tools\codex.exe",
                        }
                    ],
                },
                {
                    "surface": "claude_code",
                    "reachable_commands": [
                        {
                            "command": "claude",
                            "path": r"C:\tools\claude.exe",
                        }
                    ],
                },
            ],
        )
        self.assertIn("second real coding-agent", result["claim_ceiling"])

    def test_inventory_cli_writes_canonical_json(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "agentic_evo.cli.detect_execution_surfaces",
                return_value={
                    "schema": SURFACE_INVENTORY_SCHEMA,
                    "platform": "win32",
                    "surface_count": 1,
                    "surfaces": [{"surface": "codex", "reachable_commands": []}],
                    "claim_ceiling": "candidates only",
                },
            ),
            patch("sys.stdout", stdout),
        ):
            exit_code = main(["inventory-execution-surfaces"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "schema": SURFACE_INVENTORY_SCHEMA,
                "platform": "win32",
                "surface_count": 1,
                "surfaces": [{"surface": "codex", "reachable_commands": []}],
                "claim_ceiling": "candidates only",
            },
        )


if __name__ == "__main__":
    unittest.main()
