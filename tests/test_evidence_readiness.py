from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentic_evo.evidence_readiness import (
    EvidenceReadinessError,
    canonical_matrix_bytes,
    check_rendered,
    load_matrix,
    load_p2_stop_decision,
    render_markdown,
    validate_matrix,
    validate_p2_stop_decision,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPOSITORY_ROOT / "docs" / "engineering" / "evidence-readiness.v1.json"
RENDERED_PATH = REPOSITORY_ROOT / "docs" / "engineering" / "evidence-readiness.md"
P2_PATH = REPOSITORY_ROOT / "docs" / "engineering" / "p2-stop-decision.v1.json"
PYTHON = sys.executable


class EvidenceReadinessTests(unittest.TestCase):
    def _matrix(self) -> dict[str, object]:
        return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    def test_committed_matrix_render_and_p2_record_are_strict_and_current(self) -> None:
        matrix = load_matrix(MATRIX_PATH)
        self.assertEqual(matrix["overall_status"], "not_ready")
        self.assertEqual(MATRIX_PATH.read_bytes(), canonical_matrix_bytes(matrix))
        self.assertEqual(RENDERED_PATH.read_bytes(), render_markdown(matrix).encode("utf-8"))
        checked = check_rendered(MATRIX_PATH, RENDERED_PATH)
        self.assertEqual(checked, matrix)
        p2 = load_p2_stop_decision(P2_PATH)
        self.assertEqual(p2["decision"], "stop")
        self.assertEqual(p2["status"], "not_authorized")

    def test_closed_top_level_key_order_enums_references_and_gate_ceiling_fail_closed(self) -> None:
        matrix = self._matrix()
        reordered = {key: matrix[key] for key in reversed(tuple(matrix))}
        with self.assertRaises(EvidenceReadinessError):
            validate_matrix(reordered)

        changed_class = self._matrix()
        changed_class["provenance_classes"][1] = "promoted_fixture"
        with self.assertRaises(EvidenceReadinessError):
            validate_matrix(changed_class)

        changed_gate = self._matrix()
        changed_gate["claim_ceiling"]["ready_to_install"] = True
        with self.assertRaises(EvidenceReadinessError):
            validate_matrix(changed_gate)

        incomplete_complete = self._matrix()
        incomplete_complete["lanes"][0]["status"] = "complete"
        with self.assertRaises(EvidenceReadinessError):
            validate_matrix(incomplete_complete)

        ready_blocked = self._matrix()
        ready_blocked["overall_status"] = "ready"
        with self.assertRaises(EvidenceReadinessError):
            validate_matrix(ready_blocked)

    def test_stale_render_is_rejected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix = root / "matrix.json"
            rendered = root / "rendered.md"
            matrix.write_bytes(MATRIX_PATH.read_bytes())
            rendered.write_text("stale\n", encoding="utf-8")
            before = rendered.read_bytes()
            with self.assertRaises(EvidenceReadinessError):
                check_rendered(matrix, rendered)
            self.assertEqual(rendered.read_bytes(), before)

    def test_p2_integrity_and_project_local_non_applicability_are_enforced(self) -> None:
        record = json.loads(P2_PATH.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(record)
        tampered["does_not_apply_to"].remove("other repositories")
        with self.assertRaises(EvidenceReadinessError):
            validate_p2_stop_decision(tampered)
        tampered = copy.deepcopy(record)
        tampered["blocked_by"].append("unreviewed change")
        with self.assertRaises(EvidenceReadinessError):
            validate_p2_stop_decision(tampered)

    def test_render_cli_writes_the_same_deterministic_bytes_to_an_explicit_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix = root / "matrix.json"
            output = root / "readiness.md"
            matrix.write_bytes(MATRIX_PATH.read_bytes())
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
            result = subprocess.run(
                [
                    PYTHON,
                    "-m",
                    "agentic_evo.cli",
                    "render-evidence-readiness",
                    "--matrix",
                    str(matrix),
                    "--output",
                    str(output),
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), RENDERED_PATH.read_bytes())

    def test_check_cli_is_read_only_and_prints_compact_json(self) -> None:
        matrix_before = MATRIX_PATH.read_bytes()
        rendered_before = RENDERED_PATH.read_bytes()
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
        result = subprocess.run(
            [
                PYTHON,
                "-m",
                "agentic_evo.cli",
                "check-evidence-readiness",
                "--matrix",
                "docs/engineering/evidence-readiness.v1.json",
                "--rendered",
                "docs/engineering/evidence-readiness.md",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(result.stdout.encode("utf-8"), (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"))
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["result"]["overall_status"], "not_ready")
        self.assertEqual(MATRIX_PATH.read_bytes(), matrix_before)
        self.assertEqual(RENDERED_PATH.read_bytes(), rendered_before)


if __name__ == "__main__":
    unittest.main()
